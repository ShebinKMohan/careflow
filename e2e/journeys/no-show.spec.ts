import { expect, test } from "@playwright/test";

import {
  answerIntake,
  boardEntries,
  callIn,
  checkIn,
  closeToken,
  kioskDeviceToken,
  kioskPage,
  measureVitals,
  nurseSetsLevel,
  type PatientScript,
  staffPage,
} from "../lib/actors";
import {
  answerTriggers,
  assertAlerts,
  assertBriefing,
  assertDecision,
  assertTokenAndRoom,
  assertWriteBack,
  callWithoutArriving,
  type JourneyContext,
  sessionToken,
} from "../lib/journey";
import { ROUTINE_WALK_IN } from "../lib/scripts";

// Journey 7 (M2-12): a no-show, persona `no-show` (keypad 100007, synthetic, an appointment today).
// The E2E stack runs the queue on e2e/config/rooms.yaml, whose grace window is 5 s (the shipped one
// is 120 s). The patient is called and never walks in: the token is re-queued once, behind the next
// patient, who is called into a room; called again and missed again, it becomes a no-show and
// leaves the board.
const PATIENT: PatientScript = {
  complaint: "I've had an itchy rash on my arm for a week",
  yes: [],
  scale: "2",
  typed: [
    [/when did (this|the .*) start/i, "About a week ago"],
    [/where is/i, "On my left forearm"],
  ],
};

test("no-show: called and not arrived, re-queued once behind the next patient, then a no-show off the board", async ({ browser }) => {
  test.setTimeout(420_000);
  const nurse = await staffPage(browser, "nurse", "/nurse");
  const kiosk = await kioskPage(browser, "normal");
  const { sessionId, patientToken } = await checkIn(kiosk, "100007");
  const turns = await answerIntake(kiosk, PATIENT);
  await measureVitals(kiosk);
  const ctx: JourneyContext = {
    sessionId,
    nurse,
    patient: kiosk,
    patientToken,
    kioskToken: await kioskDeviceToken(kiosk),
    triggers: answerTriggers(turns),
    alertsSeen: new Map(),
  };
  await assertAlerts(ctx, []);
  await assertDecision(ctx, [], { set: 4 });
  const queued = await sessionToken(sessionId);
  expect(queued.department).toBe("general");

  // The next patient (persona routine, the same level) queues behind.
  const other = await kioskPage(browser, "normal");
  const next = await checkIn(other, "100005");
  await answerIntake(other, ROUTINE_WALK_IN);
  await measureVitals(other);
  await nurse.goto(`/nurse/${next.sessionId}`);
  await nurseSetsLevel(nurse, 4);

  // Called, and never comes: the grace window ends and the token is re-queued, once, without a room.
  const first = await assertTokenAndRoom(ctx, { arrive: false });
  expect(first.grace_until, "a called token has a grace window").toBeTruthy();
  await expect.poll(async () => (await sessionToken(sessionId)).status, { message: "re-queued after the grace window", timeout: 60_000 }).toBe("waiting");
  const requeued = await sessionToken(sessionId);
  expect(requeued.token_code, "the same token, re-queued").toBe(first.token_code);
  expect(requeued.room ?? null, "the room was released").toBeNull();

  // The next patient is called before the re-queued token, and walks in.
  const nextCalled = await callIn(nurse, "general", next.sessionId);
  expect(nextCalled.room).toBeTruthy();

  // Called a second time and missed again: a no-show, not a second re-queue.
  const second = await callWithoutArriving(nurse, "general", sessionId);
  expect(second.token_code).toBe(first.token_code);
  await expect.poll(async () => (await sessionToken(sessionId)).status, { message: "a no-show after the second miss", timeout: 60_000 }).toBe("no_show");
  const board = await boardEntries("general");
  expect(board.map((entry) => entry.token_code), "a no-show leaves the board").not.toContain(first.token_code);
  expect(second.room).not.toBe(nextCalled.room); // the next patient is still in the other room
  expect(board.filter((entry) => entry.room === second.room), `${second.room} is free again`).toEqual([]);

  await closeToken(nurse, nextCalled.token_id); // the next patient was seen
  await assertBriefing(ctx);
  await assertWriteBack(ctx, []);
});
