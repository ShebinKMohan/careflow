/**
 * The checks every journey makes (M2-12). `assertJourney` runs them in the issue's order; a journey
 * whose queue steps come in between (the no-show) calls the steps itself:
 *
 *   a. `assertAlerts`: each expected alert reaches the nurse station live (the worklist, fed by
 *      /v1/ws/staff) within 2 s of the answer or reading that raised it, and no other alert is
 *      raised.
 *   b. `assertDecision`: the nurse decides on the card: sets or confirms the level, or overrides the
 *      suggestion with a coded reason (POST /v1/nurse/sessions/{id}/decision). Neither the patient
 *      nor the kiosk can decide, and a second decision is refused.
 *   c. `assertTokenAndRoom`: the token and the room show where the patient waits (kiosk or phone)
 *      and on the public board, which never shows a name.
 *   d. `assertBriefing`: every item of the doctor's briefing has a provenance from the five, and
 *      none lost its source.
 *   e. `assertWriteBack`: HAPI holds the session's Encounter once, with its QuestionnaireResponse
 *      and Observations, the briefing's Composition, and a Flag exactly when an alert was raised.
 *
 * The 2 s bound is asserted on the reference machine and reported on a CI runner, where speed
 * targets are never gates (CONVENTIONS; docs/FLAKY.md).
 */
import { expect, type Page, request, test } from "@playwright/test";

import { boardEntries, closeToken, decisionBody, nurseOverrides, type QueueToken, staffTokenFor, staffUserId, suggestedLevel, type Turn } from "./actors";
import { Hapi } from "./fhir";
import { STACK } from "./stack";

export const PROVENANCES = ["EHR", "PATIENT_STATED", "DEVICE_MEASURED", "NURSE_CONFIRMED", "MODEL_INFERRED"] as const;

/** Something the patient or a device sent that can raise an alert: an answer, or a reading. */
export interface Trigger {
  label: string;
  /** When it was sent, and when the API answered it (ms since the epoch). */
  sentAt: number;
  answeredAt: number;
}

export const answerTriggers = (turns: Turn[]): Trigger[] =>
  turns.map((turn) => ({ label: `"${turn.answer}" to "${turn.question}"`, sentAt: turn.sentAt, answeredAt: turn.answeredAt }));

/** The kiosk's vitals readings (POST /v1/sessions/{id}/vitals) from now on, as triggers: a reading
 * can raise an alert (sepsis). */
export function readingTriggers(page: Page): Trigger[] {
  const readings: Trigger[] = [];
  page.on("requestfinished", (sent) => {
    if (sent.method() !== "POST" || !/^\/v1\/sessions\/[^/]+\/vitals$/.test(new URL(sent.url()).pathname)) return;
    const timing = sent.timing();
    readings.push({ label: "a kiosk vitals reading", sentAt: timing.startTime, answeredAt: timing.startTime + timing.responseEnd });
  });
  return readings;
}

export interface JourneyContext {
  sessionId: string;
  /** The nurse station, open on the worklist while the patient answered (`watchAlerts`). */
  nurse: Page;
  /** Where the patient waits for the token and the room: the kiosk, or the phone after a hand-over. */
  patient: Page;
  /** The patient's session token (kiosk or phone): it must not be able to decide. */
  patientToken: string;
  /** The kiosk's device token: it must not be able to decide either. */
  kioskToken: string;
  /** What could have raised an alert, with timings (answers and device readings). */
  triggers: Trigger[];
  /** When each expected alert appeared on the nurse's worklist (`watchAlerts`). */
  alertsSeen: Map<string, Promise<number>>;
}

/** Set a level (confirmed when it is the suggestion), or override the suggestion with a reason. */
export type Decision = { set: number } | { override: "suggestion"; reason?: string };

export interface JourneyExpectation {
  /** The rule ids the journey must raise (Alert.rule_id), and no others. */
  alerts: string[];
  decision: Decision;
  /** Whether the write-back holds Observations: none for a child, who skips the sensors. */
  observations?: boolean;
}

/** Start watching the nurse's worklist row for each rule's alert chip: call it before the patient
 * answers. A chip that never comes resolves NaN, and `assertAlerts` fails on it. */
export function watchAlerts(nurse: Page, sessionId: string, rules: string[]): Map<string, Promise<number>> {
  const row = nurse.locator(`tr[data-session="${sessionId}"]`);
  return new Map(
    rules.map((rule) => [
      rule,
      row
        .locator(`[data-rule-id="${rule}"]`)
        .waitFor({ timeout: 200_000 })
        .then(
          () => Date.now(),
          () => Number.NaN,
        ),
    ]),
  );
}

async function get<T>(path: string, token: string): Promise<T> {
  const http = await request.newContext({ baseURL: STACK.api });
  try {
    const response = await http.get(path, { headers: { Authorization: `Bearer ${token}` } });
    expect(response.status(), `GET ${path}`).toBe(200);
    return (await response.json()) as T;
  } finally {
    await http.dispose();
  }
}

/** a. The expected alerts, live and in time, and nothing else. */
export async function assertAlerts(ctx: JourneyContext, alerts: string[]): Promise<void> {
  const triage = await get<{ alerts: { rule_id: string; raised_at: string }[] }>(`/v1/sessions/${ctx.sessionId}/triage`, await staffTokenFor("nurse"));
  expect([...new Set(triage.alerts.map((alert) => alert.rule_id))].sort(), "the alerts raised").toEqual([...alerts].sort());
  for (const rule of alerts) {
    const seenAt = (await ctx.alertsSeen.get(rule)) ?? Number.NaN;
    expect(seenAt, `${rule} reached the nurse's worklist without a reload`).toBeGreaterThan(0);
    const raisedAt = Math.min(...triage.alerts.filter((alert) => alert.rule_id === rule).map((alert) => Date.parse(alert.raised_at)));
    const trigger = ctx.triggers.find((sent) => sent.sentAt <= raisedAt && raisedAt <= sent.answeredAt + 50);
    expect(trigger, `${rule} was raised while an answer or a reading was being taken`).toBeTruthy();
    const latency = seenAt - trigger!.answeredAt;
    test.info().annotations.push({
      type: "alert latency",
      description:
        `${rule}: raised ${raisedAt - trigger!.sentAt} ms after ${trigger!.label} was sent; on the nurse's worklist ` +
        `${seenAt - trigger!.sentAt} ms after it was sent (${latency} ms after the answer)`,
    });
    if (process.env.CI) {
      if (latency > 2_000) test.info().annotations.push({ type: "warning", description: `${rule} on the worklist ${latency} ms after the answer (budget 2000 ms)` });
    } else {
      expect(latency, `${rule} on the worklist within 2 s of the answer`).toBeLessThanOrEqual(2_000);
    }
  }
}

/** b. The nurse decides on the card; only the nurse, and only once. Returns the level. */
export async function assertDecision(ctx: JourneyContext, alerts: string[], decision: Decision): Promise<number> {
  const card = ctx.nurse;
  await card.goto(`/nurse/${ctx.sessionId}`);
  for (const rule of alerts) await expect(card.locator(`[data-testid="alert-banner"][data-rule-id="${rule}"]`)).toBeVisible();
  await expect(card.getByText(/Suggestion only|No model suggestion/).first()).toBeVisible({ timeout: 30_000 });
  const suggested = await suggestedLevel(card);
  let level: number;
  if ("set" in decision) {
    level = decision.set;
    if (suggested === level) {
      await card.getByRole("button", { name: `Confirm level ${level}` }).click();
    } else if (suggested === null) {
      await card.getByRole("button", { name: `KTAS ${level}`, exact: true }).click();
      await card.getByRole("button", { name: `Set level ${level}` }).click();
    } else {
      await nurseOverrides(card, level);
    }
  } else {
    expect(suggested, "an override needs a suggestion to override").not.toBeNull();
    level = suggested === 1 ? 2 : suggested! - 1; // the nurse judges it one level more urgent
    await nurseOverrides(card, level, decision.reason);
  }
  await expect(card.getByTestId("decision-result")).toContainText(`KTAS ${level}`);

  const http = await request.newContext({ baseURL: STACK.api });
  try {
    const nurseId = await staffUserId(card);
    const senders = [
      ["the patient", ctx.patientToken, 403],
      ["the kiosk", ctx.kioskToken, 403],
      ["the nurse, a second time", await staffTokenFor("nurse"), 409],
    ] as const;
    for (const [who, token, refused] of senders) {
      const again = await http.post(`/v1/nurse/sessions/${ctx.sessionId}/decision`, {
        headers: { Authorization: `Bearer ${token}` },
        data: decisionBody(ctx.sessionId, nurseId, level),
      });
      expect(again.status(), `${who} cannot decide`).toBe(refused);
    }
  } finally {
    await http.dispose();
  }
  return level;
}

/** The session's queue token (the nurse's view). */
export const sessionToken = async (sessionId: string) => get<QueueToken>(`/v1/sessions/${sessionId}/token`, await staffTokenFor("nurse"));

/** c. The token and the room, on the public board (waiting, then called with the room; never a
 * name) and where the patient waits, while called: the phone shows the room only then. Then the
 * patient walks in (`arrive`, default true), so the E2E stack's 5 s grace window never re-queues a
 * journey's patient; the no-show's patient never does. A called token keeps its room for the whole
 * grace window, so the patient's screen gets 5 s to show it. */
export async function assertTokenAndRoom(ctx: JourneyContext, { arrive = true }: { arrive?: boolean } = {}): Promise<QueueToken> {
  const queued = await sessionToken(ctx.sessionId);
  expect(queued.token_code).toMatch(/^[A-Z]-\d{3}$/);
  const waiting = (await boardEntries(queued.department)).filter((entry) => entry.token_code === queued.token_code);
  expect(waiting, `${queued.token_code} waits on the board once`).toEqual([{ token_code: queued.token_code, room: null, status: "waiting" }]);

  const called = await callWithoutArriving(ctx.nurse, queued.department, ctx.sessionId);
  expect(called.token_code, "one token for the session, the same everywhere").toBe(queued.token_code);
  expect(called.room, "a called patient has a room").toBeTruthy();
  const board = await boardEntries(queued.department);
  expect(board.filter((entry) => entry.token_code === queued.token_code), "called: on the board once, with the room").toEqual([
    { token_code: queued.token_code, room: called.room, status: "called" },
  ]);
  for (const entry of board) expect(Object.keys(entry).sort(), "the board carries a token, a room and a state, never a name").toEqual(["room", "status", "token_code"]);
  const status = await get<{ patient_display?: string }>(`/v1/sessions/${ctx.sessionId}/status`, ctx.patientToken);
  if (status.patient_display) expect(JSON.stringify(board)).not.toContain(status.patient_display);

  await expect(ctx.patient.getByText(queued.token_code).first()).toBeVisible({ timeout: 5_000 });
  await expect(ctx.patient.getByText(called.room!).first()).toBeVisible({ timeout: 5_000 });

  if (!arrive) return called;
  const headers = { Authorization: `Bearer ${await staffTokenFor("nurse")}` };
  const arrived = await ctx.nurse.request.post(`${STACK.api}/v1/queue/tokens/${encodeURIComponent(called.token_id)}/arrived`, { headers });
  expect(arrived.status(), `${called.token_code} walks into ${called.room}`).toBe(200);
  return (await arrived.json()) as QueueToken;
}

/** d. The doctor's briefing: every item attributed, no source missing. */
export async function assertBriefing(ctx: JourneyContext): Promise<void> {
  const doctorToken = await staffTokenFor("doctor");
  type Item = { text: string; provenance: string; source?: unknown; source_missing?: boolean };
  let items: Item[] = [];
  const http = await request.newContext({ baseURL: STACK.api });
  try {
    await expect
      .poll(
        async () => {
          const response = await http.get(`/v1/doctor/sessions/${ctx.sessionId}/briefing`, { headers: { Authorization: `Bearer ${doctorToken}` } });
          if (response.status() !== 200) return 0;
          items = ((await response.json()) as { sections: { items: Item[] }[] }).sections.flatMap((section) => section.items);
          return items.length;
        },
        { message: "the briefing has items", timeout: 30_000 },
      )
      .toBeGreaterThan(0);
  } finally {
    await http.dispose();
  }
  const untagged = items.filter((item) => !(PROVENANCES as readonly string[]).includes(item.provenance) || !item.source || item.source_missing);
  expect(untagged, "every briefing item has a provenance and a source").toEqual([]);
  test.info().annotations.push({ type: "briefing", description: `${items.length} items, all attributed` });
}

/** e. HAPI: the Encounter once, and what was written for it. */
export async function assertWriteBack(ctx: JourneyContext, alerts: string[], { observations = true }: { observations?: boolean } = {}): Promise<void> {
  const hapi = await Hapi.open();
  try {
    const encounter = await hapi.encounter(ctx.sessionId);
    const id = String(encounter.id);
    expect(await hapi.forEncounter("QuestionnaireResponse", id), "the intake's QuestionnaireResponse").toHaveLength(1);
    const written = await hapi.forEncounter("Observation", id);
    if (observations) expect(written.length, "the Observations").toBeGreaterThan(0);
    const flags = await hapi.forEncounter("Flag", id);
    if (alerts.length > 0) expect(flags.length, "an alert journey writes a Flag").toBeGreaterThanOrEqual(1);
    else expect(flags, "no alert, no Flag").toHaveLength(0);
    await expect.poll(async () => (await hapi.documents(ctx.sessionId)).length, { message: "the briefing's document Bundle, once", timeout: 30_000 }).toBe(1);
    const [document] = await hapi.documents(ctx.sessionId);
    const entries = (document as { entry?: { resource: { resourceType: string } }[] }).entry ?? [];
    expect(entries[0]?.resource.resourceType, "the document starts with its Composition").toBe("Composition");
    test.info().annotations.push({ type: "HAPI", description: `Encounter ${id}: ${written.length} Observations, ${flags.length} Flags, 1 QuestionnaireResponse, 1 Composition` });
  } finally {
    await hapi.close();
  }
}

/** a to e, in order. Returns the level decided and the called token. */
export async function assertJourney(ctx: JourneyContext, expected: JourneyExpectation): Promise<{ level: number; token: QueueToken }> {
  await assertAlerts(ctx, expected.alerts);
  const level = await assertDecision(ctx, expected.alerts, expected.decision);
  const token = await assertTokenAndRoom(ctx);
  await assertBriefing(ctx);
  await assertWriteBack(ctx, expected.alerts, { observations: expected.observations });
  return { level, token };
}

/** Call patients into `department` until `sessionId` is called, and leave its token called (the
 * no-show journey's patient never walks in). Earlier runs' patients called first are closed. */
export async function callWithoutArriving(page: Page, department: string, sessionId: string): Promise<QueueToken> {
  const headers = { Authorization: `Bearer ${await staffTokenFor("nurse")}` };
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
