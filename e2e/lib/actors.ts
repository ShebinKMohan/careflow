/**
 * The people in a journey (M1-19), each in a browser context of their own: the patient at the
 * kiosk (signed in as the dev kiosk device, as a kiosk is) and the nurse or doctor at the staff
 * app (signed in through its login page). Nothing here stubs the API: every request reaches the
 * running stack.
 */
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";

import { type Browser, type BrowserContext, devices, expect, type Page, request, type Response, test } from "@playwright/test";

import { literally, type Locale, msg } from "./i18n";
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

/** A child's age as the parent enters it on the kiosk (M2-04): years, and months under 2. */
export interface ChildAge {
  years: number;
  months?: number;
}

/** The keypad check-in of a persona, in `locale` (picked on the first screen); the session comes
 * from the keypad's response. "Who is the patient?" is answered "Me", or "My child" with `child`'s
 * age. */
export async function checkIn(page: Page, digits: string, locale: Locale = "en", child?: ChildAge): Promise<CheckedIn> {
  await page.goto("/");
  await page.getByRole("main").getByRole("button", { name: msg("kiosk", "en", locale === "en" ? "home.english" : "home.arabic") }).click();
  await expect(page).toHaveURL(/\/consent$/);
  await page.getByRole("button", { name: msg("kiosk", locale, "safety.consent.agree"), exact: true }).click();
  await expect(page).toHaveURL(/\/checkin$/);
  for (const digit of digits) await page.getByRole("button", { name: digit, exact: true }).click();
  const keypad = page.waitForResponse(
    (response) => response.url().endsWith("/v1/checkin/keypad") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: msg("kiosk", locale, "checkin.submit"), exact: true }).click();
  const answer = await keypad;
  expect(answer.status(), "the persona is found in the stack's HAPI").toBe(200);
  const started = await answer.json();
  await expect(page).toHaveURL(/\/patient$/);
  const sessionId = String(started.session_id);
  if (child) {
    await enterChild(page, sessionId, locale, child);
  } else {
    await page.getByRole("button", { name: msg("kiosk", locale, "safety.child.me"), exact: true }).click();
  }
  await expect(page).toHaveURL(/\/intake$/);
  return { sessionId, patientToken: String(started.session_token) };
}

/** "My child", the age, then "Continue": the kiosk sends it once to POST /v1/sessions/{id}/patient. */
async function enterChild(page: Page, sessionId: string, locale: Locale, child: ChildAge): Promise<void> {
  const t = (key: string) => msg("kiosk", locale, key);
  await page.getByTestId("patient-child").click();
  const years = page.getByRole("group", { name: t("safety.child.yearsLabel") });
  await years.getByRole("button", { name: child.years === 0 ? t("safety.child.underOne") : String(child.years), exact: true }).click();
  if (child.years < 2) {
    const months = page.getByRole("group", { name: t("safety.child.monthsLabel") });
    await months.getByRole("button", { name: String(child.months ?? 0), exact: true }).click();
  }
  const sent = page.waitForResponse(
    (response) => response.url().endsWith(`/v1/sessions/${sessionId}/patient`) && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: t("safety.child.continue"), exact: true }).click();
  expect((await sent).status(), "the kiosk records the child and the age").toBe(200);
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

/** Answer the intake to its end (or its first `limit` questions): the complaint typed, then every
 * question as the script says, on a kiosk showing `locale`. */
export async function answerIntake(
  page: Page,
  script: PatientScript,
  { limit = 12, locale = "en" }: { limit?: number; locale?: Locale } = {},
): Promise<Turn[]> {
  const turns: Turn[] = [];
  const t = (key: string) => msg("kiosk", locale, key);
  const vitals = new RegExp(["vitals.measure", "vitals.continue", "vitals.skip"].map((key) => literally(t(key)).source).join("|"));
  for (let asked = 0; asked < limit; asked += 1) {
    const heading = page.getByRole("heading", { level: 2 });
    await expect(heading.or(page.getByRole("button", { name: vitals })).first()).toBeVisible();
    if (!/\/intake$/.test(page.url())) break;
    const question = (await heading.textContent())?.trim() ?? "";
    const choices = page.getByRole("group", { name: t("intake.tapLabel") });
    const scale = page.getByRole("group", { name: t("intake.scaleLabel") });
    let mode: Turn["mode"] = "tap";
    let answer: string;
    let click: () => Promise<void>;
    const typeIt = async () => {
      await page.getByLabel(t("intake.typeLabel")).fill(answer);
      await page.getByRole("button", { name: t("intake.send"), exact: true }).click();
    };
    if (turns.length === 0) {
      mode = "text";
      answer = script.complaint;
      click = typeIt;
    } else if (await choices.getByRole("button", { name: t("intake.answers.yes"), exact: true }).isVisible()) {
      answer = t(script.yes.some((pattern) => pattern.test(question)) ? "intake.answers.yes" : "intake.answers.no");
      click = () => choices.getByRole("button", { name: answer, exact: true }).click();
    } else if (await choices.getByRole("button", { name: t("intake.answers.ok"), exact: true }).isVisible()) {
      answer = t("intake.answers.ok");
      click = () => choices.getByRole("button", { name: answer, exact: true }).click();
    } else if (await scale.isVisible()) {
      answer = script.scale;
      click = () => scale.getByRole("button", { name: answer, exact: true }).click();
    } else {
      mode = "text";
      answer = script.typed.find(([pattern]) => pattern.test(question))?.[1] ?? t("intake.answers.no");
      click = typeIt;
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

/** The phone (companion) answers the rest of the intake, as the script says, until it is done.
 * `answered` is how many answers the kiosk already gave (the complaint was one of them); the phone
 * shows the session's `locale`. */
export async function answerOnPhone(page: Page, script: PatientScript, answered: number, locale: Locale = "en"): Promise<Turn[]> {
  const turns: Turn[] = [];
  const t = (key: string) => msg("companion", locale, key);
  const done = page.getByText(t("intake.done"));
  for (let asked = answered; asked < 12; asked += 1) {
    const heading = page.getByRole("heading", { level: 2 });
    await expect(heading.or(done).first()).toBeVisible({ timeout: 30_000 });
    if (await done.isVisible()) break;
    const question = (await heading.textContent())?.trim() ?? "";
    const scale = page.getByRole("group", { name: t("intake.scale") });
    const yes = page.getByRole("button", { name: t("intake.answers.yes"), exact: true });
    let mode: Turn["mode"] = "tap";
    let answer: string;
    let click: () => Promise<void>;
    if (await yes.isVisible()) {
      answer = t(script.yes.some((pattern) => pattern.test(question)) ? "intake.answers.yes" : "intake.answers.no");
      click = () => page.getByRole("button", { name: answer, exact: true }).click();
    } else if (await scale.isVisible()) {
      answer = script.scale;
      click = () => scale.getByRole("button", { name: answer, exact: true }).click();
    } else {
      mode = "text";
      answer = script.typed.find(([pattern]) => pattern.test(question))?.[1] ?? t("intake.answers.no");
      click = async () => {
        await page.getByLabel(t("intake.typeAnswer")).fill(answer);
        await page.getByRole("button", { name: t("intake.send"), exact: true }).click();
      };
    }
    const sent = page.waitForResponse(
      (response) => /\/v1\/sessions\/[^/]+\/turns$/.test(new URL(response.url()).pathname) && response.request().method() === "POST",
    );
    const sentAt = Date.now();
    await click();
    const response = await sent;
    expect(response.status(), `the phone's answer to "${question}" is accepted`).toBe(200);
    turns.push({ question, mode, answer, sentAt, answeredAt: Date.now() });
    if ((await response.json()).done) break;
    await expect(heading.or(done).first()).not.toHaveText(question, { timeout: 30_000 });
  }
  await expect(done).toBeVisible({ timeout: 30_000 });
  return turns;
}

export interface Sent {
  method: string;
  url: string;
  body: string | null;
}

/** Every request the given browser contexts send, with its URL and body: what a network capture
 * of the kiosk and the phone would show. */
export class RequestLog {
  readonly requests: Sent[] = [];

  watch(context: BrowserContext): void {
    context.on("request", (request) => {
      this.requests.push({ method: request.method(), url: request.url(), body: request.postData() });
    });
  }

  /** The requests that carry `secret` anywhere: in the URL or in the body. */
  carrying(secret: string): Sent[] {
    return this.requests.filter((sent) => sent.url.includes(secret) || (sent.body ?? "").includes(secret));
  }
}

export const isClaim = (response: Response) =>
  new URL(response.url()).pathname === "/v1/checkin/qr/claim" && response.request().method() === "POST";

/** The kiosk shows its code; the phone's camera reads it: here, the code from the kiosk's own
 * `POST /qr` answer, the value inside the QR image (`{companion}/claim#t=<code>`). */
export async function showCode(kiosk: Page): Promise<string> {
  const issued = kiosk.waitForResponse(
    (response) => /\/v1\/sessions\/[^/]+\/qr$/.test(new URL(response.url()).pathname) && response.request().method() === "POST",
  );
  await kiosk.getByTestId("continue-on-phone").click();
  const answer = await issued;
  expect(answer.status(), "the kiosk gets a hand-over code").toBe(200);
  await expect(kiosk.getByTestId("handover-qr")).toBeVisible();
  return String((await answer.json()).qr_token);
}

export async function phonePage(browser: Browser, log?: RequestLog): Promise<Page> {
  const context = await browser.newContext({ ...devices["Pixel 7"], baseURL: STACK.companion });
  log?.watch(context);
  return context.newPage();
}

/** The nurse sets this level on the open card: the suggestion confirmed when it is that level,
 * otherwise an override with a coded reason (or the level chosen when there is no suggestion). */
export async function nurseSetsLevel(page: Page, level: number): Promise<void> {
  await expect(page.getByText(/Suggestion only|No model suggestion/).first()).toBeVisible({ timeout: 30_000 });
  const suggested = await suggestedLevel(page);
  if (suggested === level) {
    await page.getByRole("button", { name: `Confirm level ${level}` }).click();
  } else if (suggested === null) {
    await page.getByRole("button", { name: `KTAS ${level}`, exact: true }).click();
    await page.getByRole("button", { name: `Set level ${level}` }).click();
  } else {
    await nurseOverrides(page, level);
  }
  await expect(page.getByTestId("decision-result")).toContainText(`KTAS ${level}`);
}

/** "I feel worse" on the phone's waiting room, with these danger answers; resolves when the phone
 * says the nurse was told, with the time the patient pressed "Tell the nurse" (ms since epoch). */
export async function pressFeelsWorse(
  phone: Page,
  answers: { chest?: string[]; fast?: string[]; drowsy?: "Yes" | "No" | "Not sure" } = {},
): Promise<number> {
  await phone.getByTestId("feels-worse").click();
  const dialog = phone.getByRole("dialog", { name: "Tell the nurse what changed" });
  for (const label of [...(answers.chest ?? []), ...(answers.fast ?? [])]) {
    await dialog.getByRole("button", { name: label, exact: true }).click();
  }
  await dialog.getByRole("button", { name: answers.drowsy ?? "No", exact: true }).click();
  const sent = phone.waitForResponse(
    (response) => new URL(response.url()).pathname === "/v1/companion/feels-worse" && response.request().method() === "POST",
  );
  const pressedAt = Date.now();
  await dialog.getByRole("button", { name: "Tell the nurse", exact: true }).click();
  expect((await sent).status(), "the press is accepted").toBe(202);
  await expect(dialog.getByTestId("feels-worse-told")).toHaveText("The nurse has been told. Stay where you are.");
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  return pressedAt;
}

/** The simulated vitals (the scenario set at sign-in), then the done screen. */
export async function measureVitals(page: Page, locale: Locale = "en"): Promise<void> {
  await expect(page).toHaveURL(/\/vitals$/, { timeout: 30_000 });
  const pulse = page.getByRole("region", { name: msg("kiosk", locale, "vitals.heartRate") });
  await expect(pulse.getByTestId("vital-value")).not.toHaveText("–", { timeout: 60_000 });
  await page.getByRole("button", { name: msg("kiosk", locale, "vitals.continue"), exact: true }).click();
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
