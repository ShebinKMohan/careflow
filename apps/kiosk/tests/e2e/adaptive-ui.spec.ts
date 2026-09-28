import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { expect, type Page, test } from "@playwright/test";

// Visual/browser evidence only: API fixtures, no model invocation and no actual room capture.
const requireStaff = createRequire(new URL("../../../staff/package.json", import.meta.url));
const AxeBuilder = requireStaff("@axe-core/playwright").default;
const screenshotsOnly = process.env.ADAPTIVE_UI_SCREENSHOTS_ONLY === "1";
const OUTPUT = resolve(process.cwd(), "../../docs/evidence/ADAPTIVE-20260929/ui");
const messages = Object.fromEntries(["en", "ar"].map(lang => [lang, JSON.parse(readFileSync(resolve(process.cwd(), `messages/${lang}.json`), "utf8"))]));
const SESSION = "32dab873-42f8-47c7-9248-9a3a73aab468";
const DEVICE = { token: "fixture-device-token", clinicId: "clinic_demo", deviceId: "fixture-device" };
const devices = [
  { name: "desktop-1440", width: 1440, height: 1000, locale: "en" },
  { name: "desktop-1280", width: 1280, height: 800, locale: "en" },
  { name: "phone-390", width: 390, height: 844, locale: "en" },
  { name: "desktop-1280", width: 1280, height: 800, locale: "ar" },
  { name: "phone-390", width: 390, height: 844, locale: "ar" },
] as const;

test.use({ launchOptions: { args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] } });
test.describe.configure({ mode: "default" });

async function fixtures(page: Page, locale: "en" | "ar") {
  let turns = 0;
  const unexpected: string[] = [];
  await page.addInitScript((device) => {
    sessionStorage.setItem("careflow.kiosk.device", JSON.stringify(device));
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    const tracks: MediaStreamTrack[] = [];
    Object.defineProperty(window, "__qaCameraTracks", { value: tracks });
    navigator.mediaDevices.getUserMedia = async (constraints) => {
      const stream = await original(constraints);
      tracks.push(...stream.getVideoTracks());
      return stream;
    };
  }, DEVICE);
  await page.route("**/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/reception/directory")) return route.fulfill({ json: { demo: true, hospital_name: "Northstar General Hospital", notice: "Synthetic demonstration directory", departments: [] } });
    if (path.endsWith("/checkin/start/qr")) return route.fulfill({ json: { qr_token: "synthetic-visual-fixture", expires_at: new Date(Date.now() + 600_000).toISOString() } });
    if (path.endsWith("/checkin/adaptive")) return route.fulfill({ json: { session_id: SESSION, session_token: "fixture-patient-token", patient_display: "A. M.", language: locale, first_question: { question_id: "q-demo-chief", key: "chief_complaint", text: locale === "en" ? "What is the main reason for your visit today?" : "ما السبب الرئيسي لزيارتك اليوم؟", notice: null } } });
    if (path.endsWith("/turns")) {
      turns += 1;
      await new Promise(resolve => setTimeout(resolve, 700));
      const question = turns === 1 ? { question_id: "q-demo-pain", key: "pain_score", text: locale === "en" ? "From 0 to 10, how strong is the pain?" : "من 0 إلى 10، ما شدة الألم؟", notice: null } : null;
      return route.fulfill({ json: { turn_id: `fixture-turn-${turns}`, assistant_text: question?.text ?? "Thank you", next_question: question, done: turns >= 2 } });
    }
    if (path.endsWith("/intake-summary")) return route.fulfill({ json: {
      session_id: SESSION, visit_reference: "CF-DEMO1234", nurse_confirmed: false, assistance_requested: false, warnings: [],
      alerts: turns ? [{ code: "CHEST_PAIN_ECG_10MIN", severity: "high" }] : [],
      suggestion: turns >= 2 ? { model_id: "laya-ktas-demo", model_version: "fixture-only-not-live-model", level: 2, scale: "KTAS", confidence: .72, reasons: [{ text: locale === "en" ? "Reported chest discomfort supports prompt nurse review." : "ألم الصدر المبلغ عنه يستدعي مراجعة تمريضية سريعة.", fact_ids: ["chief"] }] } : null,
      facts: turns ? [
        { fact_id: "chief", key: "chief_complaint", value: locale === "en" ? "Chest discomfort since this morning" : "ألم في الصدر منذ هذا الصباح", provenance: "PATIENT_STATED", simulated: false },
        { fact_id: "pain", key: "pain_score", value: "4", provenance: "PATIENT_STATED", simulated: false },
        { fact_id: "allergy", key: "ehr_context.allergies", value: "Penicillin (synthetic fixture)", provenance: "EHR", simulated: false },
      ] : [],
    } });
    if (path.endsWith("/status")) return route.fulfill({ json: { state: "awaiting_nurse", intake_revision: 2 } });
    unexpected.push(`${route.request().method()} ${path}`);
    return route.fulfill({ status: 503, json: { detail: "No visual fixture exists for this request" } });
  });
  return { unexpected };
}

async function screenshot(page: Page, filename: string) {
  await page.evaluate(() => document.fonts.ready);
  await page.mouse.move(0, 0);
  await page.screenshot({ path: resolve(OUTPUT, filename), fullPage: true, animations: "disabled" });
}

for (const device of devices) test(`fixture visual ${device.locale} ${device.name}: conversation, synthetic camera and receipt`, async ({ page, context }) => {
  test.setTimeout(90_000);
  mkdirSync(OUTPUT, { recursive: true });
  const prefix = `${device.locale}-${device.name}`;
  const t = messages[device.locale];
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await context.addCookies([{ name: "NEXT_LOCALE", value: device.locale, url: "http://localhost:3000" }]);
  await page.setViewportSize(device);
  const api = await fixtures(page, device.locale);
  await page.goto("/checkin");
  await expect(page.locator("html")).toHaveAttribute("dir", device.locale === "ar" ? "rtl" : "ltr");
  await page.getByRole("button", { name: t.reception.newPatient, exact: true }).click();
  await page.getByRole("button", { name: t.reception.visitors.self.title, exact: true }).click();
  await page.getByLabel(t.reception.name, { exact: true }).fill(device.locale === "ar" ? "مريض تجريبي" : "Alex Morgan");
  await page.getByLabel(t.reception.age, { exact: true }).fill("34");
  await page.getByRole("button", { name: t.reception.sexOptions.male, exact: true }).click();
  await page.getByRole("button", { name: t.reception.continue, exact: true }).click();
  await expect(page).toHaveURL(/\/consent$/);
  await page.getByRole("button", { name: t.safety.consent.agree, exact: true }).click();
  await page.getByRole("button", { name: t.reception.startConversation, exact: true }).click();
  await expect(page).toHaveURL(/\/intake$/);
  await expect(page.locator(".camera-preview")).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { __qaCameraTracks: MediaStreamTrack[] }).__qaCameraTracks.length)).toBe(0);
  await screenshot(page, `${prefix}-intake.png`);
  const startCamera = page.getByRole("button", { name: t.cameraPreview.start, exact: true });
  await startCamera.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".camera-preview-picture")).toHaveAttribute("data-active", "true");
  await expect.poll(() => page.locator("video").evaluate(video => (video as HTMLVideoElement).videoWidth)).toBeGreaterThan(0);
  const cameraOn = await page.evaluate(() => (window as unknown as { __qaCameraTracks: MediaStreamTrack[] }).__qaCameraTracks.map(track => ({ state: track.readyState, label: track.label })));
  expect(cameraOn).toHaveLength(1);
  expect(cameraOn[0]?.state).toBe("live");
  expect(cameraOn[0]?.label.toLowerCase()).toContain("fake");
  await screenshot(page, `${prefix}-camera-on.png`);
  const intakeAxe = screenshotsOnly ? null : await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  await page.getByRole("button", { name: t.cameraPreview.stop, exact: true }).click();
  expect(await page.evaluate(() => (window as unknown as { __qaCameraTracks: MediaStreamTrack[] }).__qaCameraTracks.every(track => track.readyState === "ended"))).toBe(true);
  expect(await page.locator("video").evaluate(video => (video as HTMLVideoElement).srcObject)).toBeNull();
  const firstExample = page.locator(".answer-examples button").first();
  await firstExample.click();
  const typed = page.getByRole("textbox", { name: t.intake.typeLabel, exact: true });
  await expect(typed).not.toHaveValue("");
  await typed.fill(device.locale === "en" ? "I have chest discomfort since this morning." : "أشعر بألم في الصدر منذ هذا الصباح.");
  await page.getByRole("button", { name: t.intake.send, exact: true }).click();
  await expect(page.getByRole("group", { name: t.intake.scaleLabel })).toBeVisible();
  await screenshot(page, `${prefix}-pain-scale.png`);
  await page.getByRole("button", { name: t.cameraPreview.start, exact: true }).click();
  await expect(page.locator(".camera-preview-picture")).toHaveAttribute("data-active", "true");
  await page.getByRole("group", { name: t.intake.scaleLabel }).getByRole("button", { name: "4", exact: true }).click();
  await expect(page).toHaveURL(/\/vitals$/);
  expect(await page.evaluate(() => (window as unknown as { __qaCameraTracks: MediaStreamTrack[] }).__qaCameraTracks.every(track => track.readyState === "ended"))).toBe(true);
  await page.getByRole("button", { name: t.vitals.guide.skip, exact: true }).click();
  await expect(page).toHaveURL(/\/done$/);
  await expect(page.getByText("CF-DEMO1234", { exact: true })).toBeVisible();
  await expect(page.getByText("fixture-only-not-live-model", { exact: true })).toBeVisible();
  const reference = page.getByText("CF-DEMO1234", { exact: true });
  await expect(reference).toHaveCSS("direction", "ltr");
  await screenshot(page, `${prefix}-receipt.png`);
  const receiptAxe = screenshotsOnly ? null : await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const overflow = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
  const report = { fixtureOnly: true, viewport: device, cameraOn, cameraStopped: true, cameraStoppedOnNavigation: true, overflow, unexpectedRequests: api.unexpected, pageErrors: errors, accessibility: { intake: intakeAxe?.violations, receipt: receiptAxe?.violations } };
  if (!screenshotsOnly) {
    writeFileSync(resolve(OUTPUT, `${prefix}-report.json`), JSON.stringify(report, null, 2));
    expect(intakeAxe.violations, "Intake WCAG A/AA findings").toEqual([]);
    expect(receiptAxe.violations, "Receipt WCAG A/AA findings").toEqual([]);
  }
  expect(api.unexpected).toEqual([]);
  expect(errors).toEqual([]);
  expect(overflow.document).toBeLessThanOrEqual(overflow.viewport);
});
