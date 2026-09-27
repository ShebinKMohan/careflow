import { mkdirSync, readFileSync } from "node:fs";

import { expect, type Page, type Request, test, type WebSocketRoute } from "@playwright/test";

// The nurse station against stubbed HTTP (page.route) and a stubbed staff WebSocket
// (page.routeWebSocket), with the synthetic, contract-valid fixtures in tests/fixtures/api/.
const fixture = <T,>(name: string): T =>
  JSON.parse(readFileSync(new URL(`../fixtures/api/${name}.json`, import.meta.url), "utf8")) as T;

const EVIDENCE_DIR = new URL("../../../../docs/evidence/M1-15/", import.meta.url).pathname;
const CARD = "/nurse/ses_chest_pain";
const RAISED_AT = new Date("2026-09-26T09:00:00Z");

interface Stubs {
  worklistCalls: number;
  /** After an alert is raised the API's worklist carries its chip. */
  worklistHasAlert: boolean;
  decisions: unknown[];
  vitals: unknown[];
  tasksCompleted: number;
  ticketStatus: number;
  socket: WebSocketRoute | null;
  releaseDecision: (() => void) | null;
  releaseTask: (() => void) | null;
}

async function stubApi(page: Page, options: { holdDecision?: boolean; holdTask?: boolean } = {}): Promise<Stubs> {
  const stubs: Stubs = {
    worklistCalls: 0,
    worklistHasAlert: false,
    decisions: [],
    vitals: [],
    tasksCompleted: 0,
    ticketStatus: 201,
    socket: null,
    releaseDecision: null,
    releaseTask: null,
  };
  await page.addInitScript(() => window.sessionStorage.setItem("careflow.staff.token", "example-nurse-token"));
  await page.route("**/v1/nurse/worklist", (route) => {
    stubs.worklistCalls += 1;
    const items = fixture<{ session_id: string; alerts: unknown[] }[]>("worklist");
    if (stubs.worklistHasAlert) {
      const chip = { alert_id: "alr_chest_pain", rule_id: "CHEST_PAIN_ECG_10MIN", severity: "critical", partial: false };
      for (const item of items) if (item.session_id === "ses_chest_pain") item.alerts = [chip];
    }
    return route.fulfill({ json: items });
  });
  await page.route("**/v1/ws-ticket", (route) =>
    stubs.ticketStatus === 201
      ? route.fulfill({ status: 201, json: { ticket: "tkt_e2e_0123456789abcdef", channel: "staff", expires_at: "2026-09-26T09:00:30Z" } })
      : route.fulfill({ status: stubs.ticketStatus, json: { title: "Unavailable", status: stubs.ticketStatus } }),
  );
  await page.route("**/v1/sessions/*/triage", (route) => route.fulfill({ json: fixture("triage") }));
  await page.route("**/v1/sessions/*/facts", (route) => route.fulfill({ json: fixture("facts") }));
  await page.route("**/v1/auth/me", (route) => route.fulfill({ json: fixture("me") }));
  await page.route("**/v1/nurse/sessions/*/decision", async (route) => {
    stubs.decisions.push(route.request().postDataJSON());
    if (options.holdDecision) await new Promise<void>((resolve) => (stubs.releaseDecision = resolve));
    return route.fulfill({ json: fixture("decision") });
  });
  await page.route("**/v1/nurse/sessions/*/vitals", (route) => {
    stubs.vitals.push(route.request().postDataJSON());
    return route.fulfill({ json: fixture("triage-after-vitals") });
  });
  await page.route("**/v1/nurse/sessions/*/tasks/*/complete", async (route) => {
    stubs.tasksCompleted += 1;
    if (options.holdTask) await new Promise<void>((resolve) => (stubs.releaseTask = resolve));
    return route.fulfill({ json: fixture("task-done") });
  });
  await page.routeWebSocket(/\/v1\/ws\/staff/, (socket) => {
    stubs.socket = socket;
  });
  return stubs;
}

test("an alert.raised pushed over the live channel shows a banner with its evidence within 1 s", async ({ page }) => {
  await page.clock.install({ time: new Date(RAISED_AT.getTime() + 60_000) });
  const stubs = await stubApi(page);
  await page.goto("/nurse");
  await expect(page.getByTestId("live-status")).toHaveText("Live");
  const rows = page.getByRole("row");
  await expect(rows.nth(1)).toContainText("M. S.");  // danger first: KTAS 2 before KTAS 5
  await expect(rows.nth(1)).toContainText("58 y, Male");
  await expect(rows.nth(1)).toContainText("Suggested KTAS 2");
  await expect(rows.nth(2)).toContainText("L. K.");

  const started = Date.now();
  stubs.worklistHasAlert = true;
  stubs.socket!.send(JSON.stringify(fixture("alert-raised")));
  const banner = page.getByTestId("alert-banner");
  await expect(banner).toBeVisible({ timeout: 1_000 });
  await expect(banner.getByTestId("alert-evidence")).toContainText("Crushing chest pain for about an hour", { timeout: 1_000 });
  const elapsed = Date.now() - started;
  console.log(`alert.raised -> banner with evidence in ${elapsed} ms`);
  expect(elapsed).toBeLessThan(1_000);
  await expect(banner).toContainText("Critical");
  await expect(banner).toContainText("Chest pain: ECG in 10 min");
  await expect(banner).toContainText("Chest pain: ECG within 10 minutes of arrival.");
  await expect(banner.getByTestId("alert-evidence")).toContainText("112 /min");
  await expect(banner.getByTestId("alert-evidence")).toContainText("Patient said");
  await expect(banner.getByTestId("alert-evidence")).toContainText("Device");
  await expect(banner.getByTestId("ecg-status")).toContainText("09:00");
  // The event also refetched the worklist, whose row now carries the alert chip.
  await expect(rows.nth(1)).toContainText("Critical: Chest pain: ECG in 10 min");

  mkdirSync(EVIDENCE_DIR, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `${EVIDENCE_DIR}nurse-worklist-alert.png`, fullPage: true });
});

test("the ECG countdown shows 01:00 after 9 minutes and Overdue +01:00 after 11", async ({ page }) => {
  await page.clock.install({ time: RAISED_AT });
  await stubApi(page);
  await page.goto(CARD);
  const status = page.getByTestId("ecg-status");
  await expect(status).toContainText("10:00");
  await page.clock.fastForward("09:00");
  await expect(status).toContainText("01:00");
  await expect(status).toHaveClass(/text-danger/);  // red under 2 minutes
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `${EVIDENCE_DIR}nurse-card-countdown.png`, fullPage: true });
  await page.clock.fastForward("02:00");
  await expect(status).toContainText("Overdue +01:00");
});

test("ECG done sends the task-complete request once and stops the countdown", async ({ page }) => {
  await page.clock.install({ time: RAISED_AT });
  const stubs = await stubApi(page, { holdTask: true });
  await page.goto(CARD);
  await page.clock.fastForward("03:00");
  await expect(page.getByTestId("ecg-status")).toContainText("07:00");
  const done = page.getByRole("button", { name: "ECG done" });
  await done.click();
  await expect.poll(() => stubs.tasksCompleted).toBe(1);
  // A second tap while the first request is still out never sends it twice.
  await done.click({ timeout: 500, force: true }).catch(() => undefined);
  stubs.releaseTask!();
  await expect(page.getByTestId("ecg-status")).toHaveText("ECG done 1:08 PM");  // 09:08:30Z in Asia/Dubai
  await page.clock.fastForward("10:00");
  await expect(page.getByTestId("ecg-status")).toHaveText("ECG done 1:08 PM");
  expect(stubs.tasksCompleted).toBe(1);
});

test("confirm sends CONFIRM with the suggested level; the token and room show only after success", async ({ page }) => {
  const stubs = await stubApi(page, { holdDecision: true });
  await page.goto(CARD);
  await expect(page.getByText("Suggestion only. You decide.")).toBeVisible();
  await expect(page.getByText("Suggested KTAS 2")).toBeVisible();
  await expect(page.getByText("Confidence 71%")).toBeVisible();
  await expect(page.getByText("Extraction incomplete. Check answers with the patient.")).toBeVisible();
  await page.getByRole("button", { name: "Confirm level 2" }).click();
  await expect.poll(() => stubs.decisions.length).toBe(1);
  await expect(page.getByTestId("decision-result")).toHaveCount(0);  // no final level before the API answers
  stubs.releaseDecision!();
  await expect(page.getByTestId("decision-result")).toContainText("KTAS 2, set by the nurse");
  await expect(page.getByTestId("decision-result")).toContainText("Number A-014 · Room Resus 2");
  await expect(page.getByTestId("alert-banner")).toHaveCount(0);  // banners stay until the triage is confirmed
  expect(stubs.decisions[0]).toMatchObject({
    session_id: "ses_chest_pain",
    suggestion_id: "sug_chest_pain",
    action: "CONFIRM",
    final_level: 2,
    scale: "KTAS",
    nurse_user_id: "nurse_demo_01",
    acknowledged_alert_ids: ["alr_chest_pain"],
  });
  await page.screenshot({ path: `${EVIDENCE_DIR}nurse-card-confirmed.png`, fullPage: true });
});

test("an override stays disabled until a reason is given, then sends OVERRIDE with it", async ({ page }) => {
  const stubs = await stubApi(page);
  await page.goto(CARD);
  await page.getByRole("button", { name: "Change level" }).click();
  await page.getByRole("button", { name: "KTAS 1", exact: true }).click();
  const submit = page.getByRole("button", { name: "Set level 1" });
  await expect(submit).toBeDisabled();
  await page.getByLabel("Or describe the reason (at least 5 characters)").fill("worse");
  await expect(submit).toBeEnabled();
  await page.getByLabel("Or describe the reason (at least 5 characters)").fill("wors");
  await expect(submit).toBeDisabled();
  await page.getByRole("button", { name: "Patient deteriorated" }).click();
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect.poll(() => stubs.decisions.length).toBe(1);
  expect(stubs.decisions[0]).toMatchObject({
    action: "OVERRIDE",
    final_level: 1,
    override_reason_code: "patient_deteriorated",
    override_note: "wors",
  });
});

test("with no model suggestion the nurse must choose a level, sent as an override with the reason pre-filled", async ({ page }) => {
  const stubs = await stubApi(page);
  await page.route("**/v1/sessions/*/triage", (route) =>
    route.fulfill({ json: { ...fixture<Record<string, unknown>>("triage"), suggestion: null } }),
  );
  await page.goto(CARD);
  await expect(page.getByText("No model suggestion. Choose a level.")).toBeVisible();
  await expect(page.getByRole("button", { name: /^Confirm level/ })).toHaveCount(0);
  await expect(page.getByText("Suggestion only. You decide.")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Choose a level" })).toBeDisabled();
  await page.getByRole("button", { name: "KTAS 2", exact: true }).click();
  await page.getByRole("button", { name: "Set level 2" }).click();
  await expect.poll(() => stubs.decisions.length).toBe(1);
  expect(stubs.decisions[0]).toMatchObject({
    suggestion_id: null,
    action: "OVERRIDE",
    final_level: 2,
    override_reason_code: "other",
    override_note: "no model suggestion",
  });
});

test("the NEWS2 form posts the values and shows the returned total without a partial badge", async ({ page }) => {
  const stubs = await stubApi(page);
  await page.goto(CARD);
  await expect(page.getByTestId("news2-partial")).toHaveText("Partial: 3 of 7 missing");
  await expect(page.getByLabel("Systolic blood pressure (mmHg)")).toHaveValue("142");
  await expect(page.getByText("From device, confirm")).toBeVisible();
  await page.getByLabel("Respiratory rate (/min)").fill("22");
  await page.getByRole("button", { name: "Alert", exact: true }).click();
  await page.getByRole("group", { name: "On supplemental oxygen" }).getByRole("button", { name: "No" }).click();
  await page.getByRole("button", { name: "Save vitals" }).click();
  await expect(page.getByTestId("news2-summary")).toContainText("NEWS2 3");
  await expect(page.getByTestId("news2-summary")).toContainText("Low risk");
  await expect(page.getByTestId("news2-partial")).toHaveCount(0);
  expect(stubs.vitals).toEqual([{ respiratory_rate: 22, systolic_bp: 142, acvpu: "A", on_supplemental_o2: false }]);
});

test("a closed live channel shows Live updates paused and the worklist is polled", async ({ page }) => {
  await page.clock.install();
  const stubs = await stubApi(page);
  await page.goto("/nurse");
  await expect(page.getByTestId("live-status")).toHaveText("Live");
  stubs.ticketStatus = 503;  // reconnects fail too, so the page stays on polling
  const before = stubs.worklistCalls;
  await stubs.socket!.close();
  await expect(page.getByTestId("live-status")).toHaveText("Live updates paused. Checking every 5 seconds.");
  await page.clock.fastForward(5_000);
  await expect.poll(() => stubs.worklistCalls).toBeGreaterThanOrEqual(before + 1);
  await page.clock.fastForward(5_000);
  await expect.poll(() => stubs.worklistCalls).toBeGreaterThanOrEqual(before + 2);
});

test("the Enter key never confirms or sets a level", async ({ page }) => {
  const stubs = await stubApi(page);
  await page.goto(CARD);
  const confirm = page.getByRole("button", { name: "Confirm level 2" });
  await expect(confirm).not.toBeFocused();  // never auto-focused
  await confirm.focus();
  await page.keyboard.press("Enter");
  await page.locator("body").press("Enter");
  await page.getByRole("button", { name: "Change level" }).click();
  await page.getByRole("button", { name: "KTAS 3", exact: true }).click();
  await page.getByRole("button", { name: "Clinical judgement" }).click();
  await page.getByRole("button", { name: "Set level 3" }).focus();
  await page.keyboard.press("Enter");
  await page.waitForTimeout(300);
  expect(stubs.decisions).toEqual([]);
  await page.getByRole("button", { name: "Set level 3" }).click();  // a real tap still works
  await expect.poll(() => stubs.decisions.length).toBe(1);
});

test("requests carry the nurse's token, never a ticket for the REST API", async ({ page }) => {
  await stubApi(page);
  const requests: Request[] = [];
  page.on("request", (request) => requests.push(request));
  await page.goto("/nurse");
  await expect(page.getByTestId("live-status")).toHaveText("Live");
  const worklist = requests.find((request) => request.url().endsWith("/v1/nurse/worklist"));
  expect(worklist?.headers().authorization).toBe("Bearer example-nurse-token");
});
