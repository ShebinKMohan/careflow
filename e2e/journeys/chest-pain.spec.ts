import { expect, test } from "@playwright/test";

import {
  answerIntake,
  boardEntries,
  checkIn,
  decisionBody,
  kioskPage,
  measureVitals,
  nurseDecides,
  type PatientScript,
  staffPage,
  staffToken,
  staffUserId,
} from "../lib/actors";
import { codes, Hapi } from "../lib/fhir";
import { STACK } from "../lib/stack";

// Journey 1 (M1-19): chest pain, persona `chest-pain` (keypad 100001, synthetic). The
// CHEST_PAIN_ECG_10MIN alert must reach the nurse before any level exists; the nurse records
// the ECG and decides; the kiosk shows the token; the doctor sees the patient's words with their
// source; HAPI holds the write-back once.
const PATIENT: PatientScript = {
  complaint: "I've had chest pain since this morning",
  yes: [/pain or tightness in your chest/i],
  scale: "7",
  typed: [
    [/when did (the chest pain|this) start/i, "It started at about seven this morning"],
    [/where is the pain/i, "In the middle of my chest"],
  ],
};

test("chest pain: the ECG alert reaches the nurse first, ECG done, the nurse decides, token, briefing, HAPI", async ({ browser }) => {
  const nurse = await staffPage(browser, "nurse", "/nurse", true); // through the login page
  const kiosk = await kioskPage(browser, "normal");
  const { sessionId } = await checkIn(kiosk, "100001");

  // This session's row: the stack keeps earlier runs' chest-pain patients on the worklist.
  const row = nurse.locator(`tr[data-session="${sessionId}"]`);
  const chip = row.filter({ hasText: "Critical: Chest pain: ECG in 10 min" });
  const seen = chip.waitFor({ timeout: 200_000 }).then(() => Date.now());
  const turns = await answerIntake(kiosk, PATIENT);
  const seenAt = await seen;

  // The turn that raised the alert is the one the server was processing at its `raised_at`; the
  // latency is from that turn to the chip on the nurse's worklist (one machine, one clock).
  const triage = await nurse.request.get(`${STACK.api}/v1/sessions/${sessionId}/triage`, {
    headers: { Authorization: `Bearer ${await staffToken(nurse)}` },
  });
  const alerts = (await triage.json()).alerts as { rule_id: string; raised_at: string }[];
  const raisedAt = Date.parse(alerts.find((alert) => alert.rule_id === "CHEST_PAIN_ECG_10MIN")!.raised_at);
  const trigger = turns.find((turn) => turn.sentAt <= raisedAt && raisedAt <= turn.answeredAt + 50);
  expect(trigger, "the alert was raised while a turn was being answered").toBeTruthy();
  const afterAnswer = seenAt - trigger!.answeredAt;
  const afterSend = seenAt - trigger!.sentAt;
  test.info().annotations.push({
    type: "alert latency",
    description:
      `patient sent "${trigger!.answer}" to "${trigger!.question}"; raised ${raisedAt - trigger!.sentAt} ms later; ` +
      `on the nurse's worklist ${afterSend} ms after the patient sent it (${afterAnswer} ms after the kiosk got the answer)`,
  });
  expect(afterAnswer, "the nurse's worklist shows the alert within 2 s of the turn").toBeLessThanOrEqual(2_000);

  // Safety rule 3: no level and no token before the nurse decides.
  await measureVitals(kiosk);
  await expect(kiosk.getByTestId("token-code")).toHaveCount(0);

  await nurse.goto(`/nurse/${sessionId}`);
  await expect(nurse.getByTestId("alert-banner").filter({ hasText: "Chest pain: ECG in 10 min" })).toBeVisible();
  const ecg = nurse.getByTestId("ecg-status");
  await expect(ecg).toHaveText(/\d\d:\d\d/);
  await nurse.getByRole("button", { name: "ECG done" }).click();
  await expect(ecg).toHaveText(/^ECG done /);
  const stopped = await ecg.textContent();
  await nurse.waitForTimeout(2_000); // pacing only, after the element is visible: the countdown must not move
  await expect(ecg).toHaveText(stopped ?? "");

  const level = await nurseDecides(nurse, 2);
  const result = nurse.getByTestId("decision-result");
  await expect(result).toContainText(`KTAS ${level}`);
  const kioskToken = kiosk.getByTestId("token-code");
  await expect(kioskToken).toHaveText(/^[A-Z]-\d{3}$/, { timeout: 30_000 });
  const token = (await kioskToken.textContent()) ?? "";
  await expect(result).toContainText(`Number ${token}`); // one queue assignment, the same everywhere

  // Decided once: a second decision is refused, and the session keeps the one token the kiosk and
  // the nurse show, which is on the public board once.
  const headers = { Authorization: `Bearer ${await staffToken(nurse)}` };
  const again = await nurse.request.post(`${STACK.api}/v1/nurse/sessions/${sessionId}/decision`, {
    headers,
    data: decisionBody(sessionId, await staffUserId(nurse), level),
  });
  expect(again.status(), "the session is not waiting for a second decision").toBe(409);
  const queued = (await (await nurse.request.get(`${STACK.api}/v1/sessions/${sessionId}/token`, { headers })).json()) as {
    token_code: string;
    department: string;
  };
  expect(queued.token_code).toBe(token);
  const board = await boardEntries(queued.department);
  expect(board.filter((entry) => entry.token_code === token), `${token} is on the ${queued.department} board once`).toHaveLength(1);

  const doctor = await staffPage(browser, "doctor", `/doctor/${sessionId}`);
  const briefing = doctor.getByTestId("briefing");
  await expect(briefing).toBeVisible({ timeout: 30_000 });
  const complaint = briefing.getByRole("listitem").filter({ hasText: /chest pain/i }).getByRole("button", { name: /Patient said/ }).first();
  await complaint.click();
  const mark = doctor.getByRole("dialog", { name: "Source" }).locator("mark");
  await expect(mark).toBeVisible();
  const quote = ((await mark.textContent()) ?? "").trim();
  expect(quote.length).toBeGreaterThan(0);
  expect(turns.some((turn) => turn.answer.includes(quote)), `"${quote}" is the patient's own words`).toBe(true);

  const hapi = await Hapi.open();
  try {
    const encounter = await hapi.encounter(sessionId);
    const id = String(encounter.id);
    expect(codes(encounter, "priority")).toContain(`KTAS-${level}`);
    expect(await hapi.forEncounter("Flag", id)).toHaveLength(1);
    const tasks = await hapi.forEncounter("Task", id);
    expect(tasks).toHaveLength(1);
    const task = tasks[0] as { status: string; restriction?: { period?: { end?: string } } };
    expect(task.restriction?.period?.end, "the Task carries the ECG deadline").toBeTruthy();
    expect(task.status).toBe("completed");
    expect((await hapi.forEncounter("Observation", id)).length).toBeGreaterThan(0);
    expect(await hapi.forEncounter("QuestionnaireResponse", id)).toHaveLength(1);
    await expect.poll(async () => (await hapi.documents(sessionId)).length, { timeout: 30_000 }).toBe(1);
  } finally {
    await hapi.close();
  }
});
