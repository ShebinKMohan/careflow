import { expect, test } from "@playwright/test";

import { answerIntake, checkIn, kioskDeviceToken, kioskPage, measureVitals, type PatientScript, staffPage, suggestedLevel } from "../lib/actors";
import { codes, Hapi } from "../lib/fhir";
import { answerTriggers, assertAlerts, assertBriefing, assertDecision, assertTokenAndRoom, assertWriteBack, type JourneyContext } from "../lib/journey";
import { ROUTINE_WALK_IN } from "../lib/scripts";

// Journey 5 (M1-19, M2-12): a routine walk-in, persona `routine` (keypad 100005, synthetic). No
// danger sign, so no alert; the nurse sets a level other than the suggestion, with a coded reason;
// the patient is called in; the Encounter written to HAPI carries the nurse's level, with no Flag.
const PATIENT: PatientScript = ROUTINE_WALK_IN;

test("routine walk-in: no alert, the nurse overrides with a reason, token, briefing, HAPI has the nurse's level and no Flag", async ({ browser }) => {
  const nurse = await staffPage(browser, "nurse", "/nurse");
  const kiosk = await kioskPage(browser, "normal");
  const { sessionId, patientToken } = await checkIn(kiosk, "100005");
  const turns = await answerIntake(kiosk, PATIENT);
  await measureVitals(kiosk);
  await expect(kiosk.getByTestId("token-code"), "no token before the nurse decides").toHaveCount(0);
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

  await nurse.goto(`/nurse/${sessionId}`);
  await expect(nurse.getByText(/Suggestion only|No model suggestion/).first()).toBeVisible();
  await expect(nurse.getByTestId("alert-banner")).toHaveCount(0);
  const suggested = await suggestedLevel(nurse);
  const level = await assertDecision(ctx, [], { set: suggested === null || suggested >= 5 ? 4 : suggested + 1 });
  await assertTokenAndRoom(ctx);
  await assertBriefing(ctx);
  await assertWriteBack(ctx, []);

  const hapi = await Hapi.open();
  try {
    expect(codes(await hapi.encounter(sessionId), "priority")).toContain(`KTAS-${level}`);
  } finally {
    await hapi.close();
  }
});
