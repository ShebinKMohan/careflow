/**
 * The people in a journey (M1-19), each in a browser context of their own: the patient at the
 * kiosk (signed in as the dev kiosk device, as a kiosk is) and the nurse or doctor at the staff
 * app (signed in through its login page). Nothing here stubs the API: every request reaches the
 * running stack.
 */
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";

import { type Browser, expect, type Page, request, test } from "@playwright/test";

import { BOARD_A, CLINIC_A, KIOSK_A, secret, STACK, staffUser } from "./stack";

type Scenario = "normal" | "septic" | "hypoxic";

/** How a patient answers each checklist question: by the question's words. */
export interface PatientScript {
  complaint: string;
  /** Yes/No questions answered "Yes"; every other one "No". */
  yes: RegExp[];
  /** The 0-10 answer for a severity or pain question. */
  scale: string;
  /** Typed answers to open questions, by the question's words; the default is "No". */
  typed: [RegExp, string][];
}

export interface Turn {
  question: string;
  mode: "text" | "tap";
  answer: string;
  /** When the answer was sent, and when the API answered it (ms since the epoch). */
  sentAt: number;
  answeredAt: number;
}

async function deviceToken(deviceId = KIOSK_A, deviceType: "kiosk" | "board" = "kiosk"): Promise<string> {
  const api = await request.newContext({ baseURL: STACK.api });
  try {
    const response = await api.post("/v1/auth/device-token", {
      data: { device_id: deviceId, device_type: deviceType, device_secret: secret("CAREFLOW_DEV_DEVICE_SECRET") },
    });
    expect(response.status(), `the dev ${deviceType} device signs in (make seed-dev)`).toBe(200);
    return String((await response.json()).access_token);
  } finally {
    await api.dispose();
  }
}

/** The kiosk, signed in as clinic A's kiosk, with the simulated vitals device on `scenario`. */
export async function kioskPage(browser: Browser, scenario: Scenario): Promise<Page> {
  const token = await deviceToken();
  const context = await browser.newContext({ baseURL: STACK.kiosk });
  const page = await context.newPage();
  await page.addInitScript(
    ({ device, scenario }) => {
      window.sessionStorage.setItem("careflow.kiosk.device", JSON.stringify(device));
      window.sessionStorage.setItem("careflow.kiosk.simulatorScenario", scenario);
    },
    { device: { token, clinicId: CLINIC_A, deviceId: KIOSK_A }, scenario },
  );
  return page;
}

export interface CheckedIn {
  sessionId: string;
  /** The patient's session token (the kiosk keeps it in memory only). */
  patientToken: string;
}

/** The keypad check-in of a persona; the session comes from the keypad's response. */
export async function checkIn(page: Page, digits: string): Promise<CheckedIn> {
  await page.goto("/");
  await page.getByRole("main").getByRole("button", { name: "English" }).click();
  await expect(page).toHaveURL(/\/consent$/);
  await page.getByRole("button", { name: "Agree", exact: true }).click();
  await expect(page).toHaveURL(/\/checkin$/);
  for (const digit of digits) await page.getByRole("button", { name: digit, exact: true }).click();
  const keypad = page.waitForResponse(
    (response) => response.url().endsWith("/v1/checkin/keypad") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Check in" }).click();
  const answer = await keypad;
  expect(answer.status(), "the persona is found in the stack's HAPI").toBe(200);
  const started = await answer.json();
  await expect(page).toHaveURL(/\/patient$/);
  await page.getByRole("button", { name: "Me", exact: true }).click();
  await expect(page).toHaveURL(/\/intake$/);
  return { sessionId: String(started.session_id), patientToken: String(started.session_token) };
}

/** The kiosk's device token (the device sign-in the kiosk keeps in session storage). */
export async function kioskDeviceToken(page: Page): Promise<string> {
  const token = await page.evaluate(() => {
    const raw = window.sessionStorage.getItem("careflow.kiosk.device");
    return raw ? String((JSON.parse(raw) as { token?: string }).token ?? "") : "";
  });
  if (!token) throw new Error("the kiosk is not signed in");
  return token;
}

/** Answer the intake to its end: the complaint typed, then every question as the script says. */
export async function answerIntake(page: Page, script: PatientScript): Promise<Turn[]> {
  const turns: Turn[] = [];
  for (let asked = 0; asked < 12; asked += 1) {
    const heading = page.getByRole("heading", { level: 2 });
    await expect(heading.or(page.getByRole("button", { name: /Measure vitals|Continue|Skip/ })).first()).toBeVisible();
    if (!/\/intake$/.test(page.url())) break;
    const question = (await heading.textContent())?.trim() ?? "";
    const choices = page.getByRole("group", { name: "Tap an answer" });
    const scale = page.getByRole("group", { name: "Tap a number from 0 to 10" });
    let mode: Turn["mode"] = "tap";
    let answer: string;
    let click: () => Promise<void>;
    if (turns.length === 0) {
      mode = "text";
      answer = script.complaint;
      click = async () => {
        await page.getByLabel("Or type your answer").fill(answer);
        await page.getByRole("button", { name: "Send" }).click();
      };
    } else if (await choices.getByRole("button", { name: "Yes", exact: true }).isVisible()) {
      answer = script.yes.some((pattern) => pattern.test(question)) ? "Yes" : "No";
      click = () => choices.getByRole("button", { name: answer, exact: true }).click();
    } else if (await choices.getByRole("button", { name: "OK", exact: true }).isVisible()) {
      answer = "OK";
      click = () => choices.getByRole("button", { name: "OK", exact: true }).click();
    } else if (await scale.isVisible()) {
      answer = script.scale;
      click = () => scale.getByRole("button", { name: answer, exact: true }).click();
    } else {
      mode = "text";
      answer = script.typed.find(([pattern]) => pattern.test(question))?.[1] ?? "No";
      click = async () => {
        await page.getByLabel("Or type your answer").fill(answer);
        await page.getByRole("button", { name: "Send" }).click();
      };
    }
    const sent = page.waitForResponse(
      (response) => /\/v1\/sessions\/[^/]+\/turns$/.test(new URL(response.url()).pathname) && response.request().method() === "POST",
    );
    const sentAt = Date.now();
    await click();
    const response = await sent;
    expect(response.status(), `the turn "${question}" is accepted`).toBe(200);
    turns.push({ question, mode, answer, sentAt, answeredAt: Date.now() });
    const body = await response.json();
    if (body.done) break;
    await expect(heading).not.toHaveText(question, { timeout: 30_000 });
  }
  // Measured, never a gate: how long the patient waited for each answer to be taken.
  test.info().annotations.push({
    type: "turn latency",
    description: turns.map((turn) => `${turn.mode} ${turn.answeredAt - turn.sentAt} ms`).join(", "),
  });
  return turns;
}

/** The simulated vitals (the scenario set at sign-in), then the done screen. */
export async function measureVitals(page: Page): Promise<void> {
  await expect(page).toHaveURL(/\/vitals$/, { timeout: 30_000 });
  await expect(page.getByRole("region", { name: "Pulse" }).getByTestId("vital-value")).not.toHaveText("–", { timeout: 60_000 });
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page).toHaveURL(/\/done$/);
}

type Role = "nurse" | "doctor" | "admin";
/** Staff tokens from the real login route, kept between runs and reused while the API accepts
 * them, as a signed-in person would. The API allows 5 logins a minute per address; back-to-back
 * journey runs must not trip it. The file is under the git-ignored `.run/` (owner-only), not in
 * test-results/, which Playwright empties at the start of every run. */
const AUTH_DIR = new URL("../../.run/e2e/", import.meta.url).pathname;
const AUTH_FILE = `${AUTH_DIR}staff-tokens.json`;

async function staffTokenFor(role: Role): Promise<string> {
  const api = await request.newContext({ baseURL: STACK.api });
  try {
    const cached: Partial<Record<string, string>> = existsSync(AUTH_FILE) ? JSON.parse(readFileSync(AUTH_FILE, "utf8")) : {};
    const key = `${STACK.name}:${role}`;
    const known = cached[key];
    if (known && (await api.get("/v1/auth/me", { headers: { Authorization: `Bearer ${known}` } })).status() === 200) return known;
    const response = await api.post("/v1/auth/login", { data: { username: staffUser(role), password: secret("CAREFLOW_DEV_PASSWORD") } });
    expect(response.status(), `the dev ${role} signs in (429: wait a minute, the login rate limit)`).toBe(200);
    const token = String((await response.json()).access_token);
    mkdirSync(AUTH_DIR, { recursive: true, mode: 0o700 });
    writeFileSync(AUTH_FILE, JSON.stringify({ ...cached, [key]: token }));
    chmodSync(AUTH_FILE, 0o600);
    return token;
  } finally {
    await api.dispose();
  }
}

const escaped = (path: string) => new RegExp(`${path.replace(/[?]/g, "\\?")}$`);

/** A staff member of clinic A at `next`: signed in with a token from the real login route, held
 * where the staff app keeps it. `viaLoginPage` signs in through the staff app's login page. */
export async function staffPage(browser: Browser, role: Role, next: string, viaLoginPage = false): Promise<Page> {
  const context = await browser.newContext({ baseURL: STACK.staff });
  if (!viaLoginPage) {
    const token = await staffTokenFor(role);
    await context.addInitScript((value) => window.sessionStorage.setItem("careflow.staff.token", value), token);
  }
  const page = await context.newPage();
  if (viaLoginPage) {
    await page.goto(`/login?next=${encodeURIComponent(next)}`);
    await page.getByLabel("Username").fill(staffUser(role));
    await page.getByLabel("Password").fill(secret("CAREFLOW_DEV_PASSWORD"));
    await page.getByRole("main").getByRole("button", { name: "Sign in" }).click();
  } else {
    await page.goto(next);
  }
  await expect(page).toHaveURL(escaped(next));
  return page;
}

/** The nurse's decision on the open card: confirm the suggestion, or, without one, set `level`
 * (an override with the reason the card pre-fills). Returns the level decided. */
export async function nurseDecides(page: Page, fallbackLevel: number): Promise<number> {
  const confirm = page.getByRole("button", { name: /^Confirm level \d$/ });
  if (await confirm.isVisible()) {
    const level = Number((await confirm.textContent())?.match(/(\d)$/)?.[1]);
    await confirm.click();
    return level;
  }
  await expect(page.getByText("No model suggestion. Choose a level.")).toBeVisible();
  await page.getByRole("button", { name: `KTAS ${fallbackLevel}`, exact: true }).click();
  await page.getByRole("button", { name: `Set level ${fallbackLevel}` }).click();
  return fallbackLevel;
}

/** An override on the open card: a level other than the suggestion, with a coded reason. */
export async function nurseOverrides(page: Page, level: number, reason = "Clinical judgement"): Promise<void> {
  const change = page.getByRole("button", { name: "Change level" });
  if (await change.isVisible()) await change.click();
  await page.getByRole("button", { name: `KTAS ${level}`, exact: true }).click();
  const coded = page.getByRole("button", { name: reason, exact: true });
  if (await coded.isVisible()) await coded.click();
  await page.getByRole("button", { name: `Set level ${level}` }).click();
}

/** The suggested level on the open card, or null when the model made none. */
export async function suggestedLevel(page: Page): Promise<number | null> {
  const confirm = page.getByRole("button", { name: /^Confirm level \d$/ });
  if (!(await confirm.isVisible())) return null;
  return Number((await confirm.textContent())?.match(/(\d)$/)?.[1]);
}

/** A valid decision as the staff app sends it (an override with a coded reason, valid with or
 * without a suggestion); the API checks who sends it, then whether the session is waiting. */
export function decisionBody(sessionId: string, nurseUserId: string, level: number): Record<string, unknown> {
  return {
    decision_id: `dec_${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`,
    session_id: sessionId,
    suggestion_id: null,
    action: "OVERRIDE",
    override_reason_code: "clinical_judgement",
    final_level: level,
    scale: "KTAS",
    nurse_user_id: nurseUserId,
    decided_at: new Date().toISOString(),
  };
}

/** The signed-in staff member's user id (`GET /v1/auth/me`). */
export async function staffUserId(page: Page): Promise<string> {
  const me = await page.request.get(`${STACK.api}/v1/auth/me`, { headers: { Authorization: `Bearer ${await staffToken(page)}` } });
  expect(me.status()).toBe(200);
  return String((await me.json()).user_id);
}

export interface BoardEntry {
  token_code: string;
  room: string | null;
  status: string;
}

/** Clinic A's public queue board for `department`, as its board device reads it. */
export async function boardEntries(department: string): Promise<BoardEntry[]> {
  const token = await deviceToken(BOARD_A, "board");
  const api = await request.newContext({ baseURL: STACK.api });
  try {
    const response = await api.get(`/v1/board/${encodeURIComponent(department)}`, { headers: { Authorization: `Bearer ${token}` } });
    expect(response.status(), "the board device reads its board").toBe(200);
    return (await response.json()) as BoardEntry[];
  } finally {
    await api.dispose();
  }
}

export interface QueueToken {
  token_id: string;
  session_id: string;
  token_code: string;
  department: string;
  status: string;
  room?: string | null;
}

/** The nurse calls patients into `department`, in the queue's order, until `sessionId` is called.
 * M1 has no call button yet (the queue controls are M2-07), so this is the API the staff app will
 * use. A test stack keeps earlier runs' patients waiting; each one called first is closed (seen),
 * which frees its room. */
export async function callIn(page: Page, department: string, sessionId: string): Promise<QueueToken> {
  const headers = { Authorization: `Bearer ${await staffToken(page)}` };
  for (let calls = 0; calls < 100; calls += 1) {
    const response = await page.request.post(`${STACK.api}/v1/queue/${encodeURIComponent(department)}/call-next`, { headers });
    expect(response.status(), `a ${department} room is free for the next patient`).toBe(200);
    const token = (await response.json()).token as QueueToken | null;
    if (token === null) throw new Error(`${sessionId} is not waiting in ${department}`);
    if (token.session_id === sessionId) return token;
    await closeToken(page, token.token_id);
  }
  throw new Error(`${sessionId} was not called within 100 calls`);
}

/** The patient was seen: the token is closed and its room freed. */
export async function closeToken(page: Page, tokenId: string): Promise<void> {
  const headers = { Authorization: `Bearer ${await staffToken(page)}` };
  const closed = await page.request.post(`${STACK.api}/v1/queue/tokens/${encodeURIComponent(tokenId)}/close`, { headers });
  expect(closed.status(), `token ${tokenId} closes`).toBe(200);
}

/** The signed-in staff member's API token, from the staff app's session storage. */
export async function staffToken(page: Page): Promise<string> {
  const token = await page.evaluate(() => window.sessionStorage.getItem("careflow.staff.token"));
  if (!token) throw new Error("the staff page is not signed in");
  return token;
}
