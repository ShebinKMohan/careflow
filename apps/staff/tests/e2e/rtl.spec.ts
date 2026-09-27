import { readFileSync } from "node:fs";

import { expect, type Page, test } from "@playwright/test";

// @rtl (M2-01): the nurse station and the doctor briefing in English and in Arabic, against
// stubbed HTTP and a quiet stubbed staff WebSocket. Direction and language are asserted everywhere;
// the screenshots are compared only when the browser runs in the pinned Playwright image
// (`make rtl`), because baselines differ by OS.
const json = <T,>(path: string): T => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8")) as T;
const fixture = <T,>(name: string): T => json<T>(`../fixtures/api/${name}.json`);

type Locale = "en" | "ar";
type Messages = { [key: string]: string | Messages };
const MESSAGES: Record<Locale, Messages> = { en: json("../../messages/en.json"), ar: json("../../messages/ar.json") };
/** The app's own message for `key` (dotted), so the test follows the shipped strings; the `<ltr>`
 * wrapper renders as an isolated span, so the screen shows the text without it. */
function msg(locale: Locale, key: string): string {
  const value = key.split(".").reduce<string | Messages>((node, part) => (node as Messages)[part]!, MESSAGES[locale]);
  if (typeof value !== "string") throw new Error(`${key} is not a string`);
  return value.replace(/<\/?ltr>/g, "");
}

const NOW = new Date("2026-09-26T09:01:00Z");
const CHIP = { alert_id: "alr_chest_pain", rule_id: "CHEST_PAIN_ECG_10MIN", severity: "critical", partial: false, raised_at: "2026-09-26T09:00:00Z" };
const ARABIC_QUOTE = "عندي ألم شديد في صدري منذ ساعة";

test.use({ timezoneId: "UTC" });

async function inLanguage(page: Page, baseURL: string | undefined, locale: Locale) {
  await page.context().addCookies([{ name: "NEXT_LOCALE", value: locale, url: baseURL! }]);
}

async function expectLanguage(page: Page, locale: Locale) {
  await expect(page.locator("html")).toHaveAttribute("lang", locale);
  await expect(page.locator("html")).toHaveAttribute("dir", locale === "ar" ? "rtl" : "ltr");
}

/** Freeze the clock `at` seconds after NOW (countdowns and waits stop, at the same moment on
 * every run), then compare the page. Each screenshot of a test takes a later moment. */
async function snap(page: Page, name: string, at: number) {
  await page.clock.pauseAt(new Date(NOW.getTime() + at * 1_000));
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot(`${name}.png`, { fullPage: true });
  await page.clock.resume();
}

async function stubNurseApi(page: Page) {
  await page.addInitScript(() => window.sessionStorage.setItem("careflow.staff.token", "example-nurse-token"));
  await page.route("**/v1/nurse/worklist", (route) => {
    const items = fixture<{ session_id: string; alerts: unknown[] }[]>("worklist");
    for (const item of items) if (item.session_id === "ses_chest_pain") item.alerts = [CHIP];
    return route.fulfill({ json: items });
  });
  await page.route("**/v1/ws-ticket", (route) =>
    route.fulfill({ status: 201, json: { ticket: "tkt_e2e_0123456789abcdef", channel: "staff", expires_at: "2026-09-26T09:00:30Z" } }),
  );
  await page.routeWebSocket(/\/v1\/ws\/staff/, () => {}); // connected, and quiet
  await page.route("**/v1/sessions/*/triage", (route) => route.fulfill({ json: fixture("triage") }));
  await page.route("**/v1/sessions/*/facts", (route) => route.fulfill({ json: fixture("facts") }));
  await page.route("**/v1/auth/me", (route) => route.fulfill({ json: fixture("me") }));
}

for (const locale of ["en", "ar"] as const) {
  const language = locale === "en" ? "English" : "Arabic";

  test(`@rtl the nurse station in ${language}: worklist, alert and session card`, async ({ page, baseURL }) => {
    await page.clock.install({ time: NOW });
    await inLanguage(page, baseURL, locale);
    await stubNurseApi(page);
    await page.goto("/nurse");
    await expectLanguage(page, locale);
    await expect(page.getByTestId("live-status")).toHaveText(msg(locale, "nurse.live"));
    const rows = page.getByRole("row");
    await expect(rows.nth(1)).toContainText("M. S."); // danger first, in both directions
    await expect(rows.nth(1)).toContainText(msg(locale, "safety.alerts.rules.CHEST_PAIN_ECG_10MIN"));
    await expect(rows.nth(2)).toContainText("L. K.");
    await expect(page.getByTestId("alert-banner").first()).toContainText(msg(locale, "safety.alerts.severity.critical"));
    await snap(page, `staff-${locale}-worklist`, 30);

    await page.goto("/nurse/ses_chest_pain");
    await expectLanguage(page, locale);
    const banner = page.getByTestId("alert-banner").first();
    await expect(banner).toContainText(msg(locale, "safety.alerts.rules.CHEST_PAIN_ECG_10MIN"));
    await expect(banner.getByTestId("alert-evidence")).toContainText("112");
    await expect(page.getByText(msg(locale, "safety.triage.notFinal"))).toBeVisible();
    await snap(page, `staff-${locale}-session`, 60);

    // Staff switch language from the header at any time.
    const other: Locale = locale === "en" ? "ar" : "en";
    await page.getByRole("banner").getByRole("button", { name: other === "ar" ? "العربية" : "English" }).click();
    await expectLanguage(page, other);
    await expect(page.getByText(msg(other, "safety.triage.notFinal"))).toBeVisible();
  });

  test(`@rtl the doctor briefing in ${language}, with an Arabic quote kept right to left`, async ({ page, baseURL }) => {
    await page.clock.install({ time: NOW });
    await inLanguage(page, baseURL, locale);
    const briefing = fixture<{ sections: { items: { provenance: string; source: { quote?: string }; source_missing?: boolean }[] }[] }>(
      "briefing-chest-pain",
    );
    // The patient spoke Arabic: the fact stays English-coded, the quote is the patient's words.
    const said = briefing.sections.flatMap((section) => section.items).find((item) => item.provenance === "PATIENT_STATED" && !item.source_missing)!;
    said.source.quote = ARABIC_QUOTE;
    await page.addInitScript(() => window.sessionStorage.setItem("careflow.staff.token", "example-doctor-token"));
    await page.route("**/v1/doctor/sessions/*/briefing", (route) => route.fulfill({ json: briefing }));
    await page.goto("/doctor/ses_chest_pain");
    await expectLanguage(page, locale);
    const view = page.getByTestId("briefing");
    await expect(view).toBeVisible();
    for (const provenance of ["EHR", "PATIENT_STATED", "DEVICE_MEASURED", "NURSE_CONFIRMED", "MODEL_INFERRED"]) {
      await expect(view.getByText(msg(locale, `provenance.${provenance}`), { exact: true }).first()).toBeVisible();
    }
    await snap(page, `staff-${locale}-briefing`, 30);

    const chip = view.getByRole("button", { name: new RegExp(msg(locale, "provenance.PATIENT_STATED")) }).first();
    await chip.focus();
    await page.keyboard.press("Enter");
    const quote = page.getByRole("dialog", { name: msg(locale, "source.title") }).locator("q");
    await expect(quote).toContainText(ARABIC_QUOTE);
    await expect(quote).toHaveCSS("direction", "rtl"); // dir="auto": Arabic reads right to left in either UI
    await page.keyboard.press("Escape");
    await expect(chip).toBeFocused();
  });
}
