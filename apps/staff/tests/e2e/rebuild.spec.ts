import { mkdirSync, readFileSync } from "node:fs";

import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const fixture = (name: string) => JSON.parse(readFileSync(new URL(`../fixtures/api/${name}.json`, import.meta.url), "utf8"));
const evidence = new URL("../../../../docs/evidence/UI-REBUILD-20260928/screenshots/", import.meta.url).pathname;

test("worklist search never hides a safety alert and the layout fits desktop and Arabic phone screens", async ({ page, baseURL }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.clock.install({ time: new Date("2026-09-26T09:01:00Z") });
  await page.clock.setFixedTime(new Date("2026-09-26T09:01:00Z"));
  await page.addInitScript(() => sessionStorage.setItem("careflow.staff.token", "example-nurse-token"));
  const rows = fixture("worklist");
  rows.find((row: { session_id: string }) => row.session_id === "ses_chest_pain").alerts = [
    { alert_id: "alr_chest_pain", rule_id: "CHEST_PAIN_ECG_10MIN", severity: "critical", partial: false },
  ];
  await page.route("**/v1/nurse/worklist", (route) => route.fulfill({ json: rows }));
  await page.route("**/v1/sessions/*/triage", (route) => route.fulfill({ json: fixture("triage") }));
  await page.route("**/v1/sessions/*/facts", (route) => route.fulfill({ json: fixture("facts") }));
  await page.route("**/v1/auth/me", (route) => route.fulfill({ json: fixture("me") }));
  await page.route("**/v1/ws-ticket", (route) => route.fulfill({ status: 201, json: { ticket: "tkt_ui_rebuild_fixture", channel: "staff", expires_at: "2026-09-26T10:00:00Z" } }));
  await page.routeWebSocket(/\/v1\/ws\/staff/, () => {});
  await page.goto("/nurse");
  await expect(page.getByTestId("alert-banner")).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "M. S." })).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  mkdirSync(evidence, { recursive: true });
  await page.screenshot({ path: `${evidence}staff-nurse-desktop-fixture.png`, fullPage: true });
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter((item) => item.impact === "serious" || item.impact === "critical").map((item) => `${item.id}: ${item.help}`)).toEqual([]);

  await page.getByRole("searchbox").fill("no matching patient");
  await expect(page.getByRole("row").filter({ hasText: "M. S." })).toHaveCount(0);
  await expect(page.getByTestId("alert-banner")).toBeVisible();
  await page.getByRole("searchbox").fill("");
  await expect(page.getByRole("row").filter({ hasText: "M. S." })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.context().addCookies([{ name: "NEXT_LOCALE", value: "ar", url: baseURL! }]);
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByTestId("alert-banner")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: `${evidence}staff-nurse-arabic-phone-fixture.png`, fullPage: true });
});
