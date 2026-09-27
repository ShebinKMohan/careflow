import type { Schema } from "@careflow/contracts";
import { expect, test } from "@playwright/test";

// Browser lifecycle checks against controlled, contract-shaped API responses. These do not
// assert backend/FHIR integration; API checkpoint tests cover the server separately.
const FIRST = { question_id: "q_complaint", text: "What brings you here?", key: "chief_complaint", notice: null } satisfies Schema<"IntakeQuestion">;
const SECOND = { question_id: "q_duration", text: "When did this start?", key: "symptom_onset_at", notice: null } satisfies Schema<"IntakeQuestion">;
const CLAIM = { session_id: "ses_phone", session_token: "phone-test-token", language: "en", patient_display: "L. K.", first_question: FIRST } satisfies Schema<"CheckinResult">;

test("a claimed visit survives reload, advances, and reloads at the server's current question", async ({ page }) => {
  let claims = 0;
  let question = FIRST;
  await page.route("**/v1/checkin/qr/claim", (route) => {
    claims += 1;
    return route.fulfill({ json: CLAIM });
  });
  await page.route("**/v1/sessions/ses_phone/status", (route) => route.fulfill({ json: { state: "intake", current_question: question, intake_revision: question.question_id === FIRST.question_id ? 0 : 1 } satisfies Schema<"SessionStatus"> }));
  await page.route("**/v1/sessions/ses_phone/turns", (route) => {
    question = SECOND;
    return route.fulfill({ json: { turn_id: "turn_browser", assistant_text: SECOND.text, done: false, next_question: question, intake_revision: 1 } satisfies Schema<"TurnResponse"> });
  });
  await page.goto("/claim#t=browser-qr-code-0001");
  await expect(page.getByRole("heading", { name: FIRST.text })).toBeVisible();
  await expect(page).toHaveURL(/\/claim$/);
  await page.reload();
  await expect(page.getByRole("heading", { name: FIRST.text })).toBeVisible();
  await page.getByLabel("Or type your answer").fill("A sore ankle");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByRole("heading", { name: SECOND.text })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: SECOND.text })).toBeVisible();
  await page.goto("/");
  await expect(page.getByRole("heading", { name: SECOND.text })).toBeVisible();
  expect(claims).toBe(1);
  const saved = await page.evaluate(() => JSON.stringify({ ...sessionStorage }));
  expect(saved).not.toContain("A sore ankle");
  expect(saved).not.toContain(SECOND.text);
});

test("a QR network failure can be retried and an expired saved visit can reconnect", async ({ page }) => {
  let claims = 0;
  await page.route("**/v1/checkin/qr/claim", (route) => {
    claims += 1;
    return claims === 1 ? route.abort("failed") : route.fulfill({ json: CLAIM });
  });
  await page.route("**/v1/sessions/ses_phone/status", (route) => route.fulfill({ json: { state: "intake", current_question: FIRST, intake_revision: 0 } satisfies Schema<"SessionStatus"> }));
  await page.goto("/claim#t=browser-qr-code-0002");
  await expect(page.getByRole("alert").filter({ hasText: "We could not reach the clinic" })).toBeVisible();
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: FIRST.text })).toBeVisible();
  await page.route("**/v1/sessions/ses_phone/status", (route) => route.fulfill({ status: 401, json: { status: 401 } }));
  await page.reload();
  await expect(page.getByRole("alert").filter({ hasText: "Your phone session has ended" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Enter a code" })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("careflow.companion.session-token"))).toBeNull();
});

test("a lost answer response reads the accepted question and never automatically resends", async ({ page }) => {
  let turns = 0;
  await page.route("**/v1/checkin/qr/claim", (route) => route.fulfill({ json: CLAIM }));
  await page.route("**/v1/sessions/ses_phone/status", (route) => route.fulfill({ json: { state: "intake", current_question: turns === 0 ? FIRST : SECOND, intake_revision: turns === 0 ? 0 : 1 } satisfies Schema<"SessionStatus"> }));
  await page.route("**/v1/sessions/ses_phone/turns", (route) => {
    turns += 1;
    return route.abort("failed");
  });
  await page.goto("/claim#t=browser-qr-code-0003");
  await page.getByLabel("Or type your answer").fill("A sore ankle");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByRole("heading", { name: SECOND.text })).toBeVisible();
  await expect(page.getByLabel("Or type your answer")).toBeEmpty();
  expect(turns).toBe(1);
});

test("the manual root-page visit can reconnect after expiry without a same-page navigation trap", async ({ page }) => {
  let claims = 0;
  await page.route("**/v1/checkin/qr/claim", (route) => {
    claims += 1;
    return route.fulfill({ json: CLAIM });
  });
  await page.route("**/v1/sessions/ses_phone/status", (route) => route.fulfill({ json: { state: "intake", current_question: FIRST, intake_revision: 1 } satisfies Schema<"SessionStatus"> }));
  await page.route("**/v1/sessions/ses_phone/turns", (route) => route.fulfill({ status: 401, json: { status: 401 } }));
  await page.goto("/");
  await page.getByLabel("Hand-over code").fill("manual-browser-code-0001");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByLabel("Or type your answer").fill("A sore ankle");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Your phone session has ended" })).toBeVisible();
  await page.getByRole("button", { name: "Enter a code" }).click();
  await expect(page).toHaveURL(/\/$/);
  await page.getByLabel("Hand-over code").fill("manual-browser-code-0002");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByRole("heading", { name: FIRST.text })).toBeVisible();
  expect(claims).toBe(2);
});

test("a called visit opens its room status instead of instructing the patient to keep waiting", async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem("careflow.companion.session-token", "phone-test-token");
    sessionStorage.setItem("careflow.companion.session", JSON.stringify({ sessionId: "ses_phone", language: "en", display: "L. K." }));
  });
  await page.route("**/v1/sessions/ses_phone/status", (route) => route.fulfill({ json: { state: "called", token_code: "G-031", room: "Room 4", intake_revision: 3 } satisfies Schema<"SessionStatus"> }));
  await page.route("**/v1/companion/status", (route) => route.fulfill({ json: {
    state: "called", token: { token_id: "tok_phone", session_id: "ses_phone", token_code: "G-031", department: "ed", status: "called", room: "Room 4", issued_at: "2026-09-27T08:00:00Z" },
    room: "Room 4", position: null, wait_estimate_minutes: null,
  } satisfies Schema<"CompanionStatus"> }));
  await page.goto("/claim");
  await expect(page).toHaveURL(/\/status$/);
  await expect(page.getByText("Please go to Room 4 now.")).toBeVisible();
  await expect(page.getByText(/stay in the waiting area/)).toHaveCount(0);
});
