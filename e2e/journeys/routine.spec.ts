import { expect, test } from "@playwright/test";

import {
  answerIntake,
  checkIn,
  decisionBody,
  kioskDeviceToken,
  kioskPage,
  measureVitals,
  nurseOverrides,
  type PatientScript,
  staffPage,
  staffUserId,
  suggestedLevel,
} from "../lib/actors";
import { codes, Hapi } from "../lib/fhir";
import { STACK } from "../lib/stack";

// Journey 5 (M1-19): a routine walk-in, persona `routine` (keypad 100005, synthetic). No danger
// sign, so no alert; the nurse overrides the level with a coded reason, and the Encounter written
// to HAPI carries the nurse's level, with no Flag.
const PATIENT: PatientScript = {
  complaint: "I twisted my ankle playing football yesterday",
  yes: [],
  scale: "4",
  typed: [
    [/when did (this|the pain) start/i, "Yesterday afternoon"],
    [/where is the pain/i, "My left ankle"],
  ],
};

test("routine walk-in: no alert, the nurse overrides with a reason, HAPI has the nurse's level and no Flag", async ({ browser }) => {
  const kiosk = await kioskPage(browser, "normal");
  const { sessionId, patientToken } = await checkIn(kiosk, "100005");
  await answerIntake(kiosk, PATIENT);
  await measureVitals(kiosk);
  await expect(kiosk.getByTestId("token-code")).toHaveCount(0);

  const nurse = await staffPage(browser, "nurse", `/nurse/${sessionId}`);
  // Nurse-only finalization (safety rule 3): a decision that is valid but for its sender, the
  // kiosk device or the patient, is refused.
  const nurseUserId = await staffUserId(nurse);
  for (const [sender, token] of [["kiosk device", await kioskDeviceToken(kiosk)], ["patient", patientToken]]) {
    const refused = await kiosk.request.post(`${STACK.api}/v1/nurse/sessions/${sessionId}/decision`, {
      headers: { Authorization: `Bearer ${token}` },
      data: decisionBody(sessionId, nurseUserId, 1),
    });
    expect(refused.status(), `the ${sender} cannot set a level`).toBe(403);
  }
  await expect(nurse.getByText(/Suggestion only|No model suggestion/).first()).toBeVisible();
  await expect(nurse.getByTestId("alert-banner")).toHaveCount(0);
  const suggested = await suggestedLevel(nurse);
  const level = suggested === null || suggested >= 5 ? 4 : suggested + 1;
  await nurseOverrides(nurse, level);
  await expect(nurse.getByTestId("decision-result")).toContainText(`KTAS ${level}`);
  await expect(kiosk.getByTestId("token-code")).toHaveText(/^[A-Z]-\d{3}$/, { timeout: 30_000 });

  const hapi = await Hapi.open();
  try {
    const encounter = await hapi.encounter(sessionId);
    expect(codes(encounter, "priority")).toContain(`KTAS-${level}`);
    expect(await hapi.forEncounter("Flag", String(encounter.id))).toHaveLength(0);
  } finally {
    await hapi.close();
  }
});
