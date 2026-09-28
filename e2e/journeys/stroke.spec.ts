import { expect, request, test } from "@playwright/test";

import { answerIntake, checkIn, kioskDeviceToken, kioskPage, measureVitals, type PatientScript, staffPage, staffTokenFor } from "../lib/actors";
import { answerTriggers, assertJourney, watchAlerts } from "../lib/journey";
import { STACK } from "../lib/stack";

// Journey 2 (M2-12): stroke, persona `stroke` (keypad 100002, synthetic): face droop, right arm
// weakness and slurred speech, last known well at 08:15. FAST_STROKE reaches the nurse before any
// level, on the patient's own answers; the nurse overrides the suggested level with a coded reason;
// another clinic's nurse never sees the patient. No stroke-team contact is claimed: none exists.
const PATIENT: PatientScript = {
  complaint: "My face feels droopy on the right and my right arm has gone weak",
  yes: [/face drooping/i, /arms weak/i, /speech slurred/i],
  scale: "3",
  typed: [[/last feel completely normal/i, "At quarter past eight this morning"]],
};

type Fact = { value: unknown; provenance: string; source: { kind: string; quote?: string } };

test("stroke: FAST reaches the nurse first, the nurse overrides with a reason, token, briefing, HAPI; clinic B sees nothing", async ({ browser }) => {
  const nurse = await staffPage(browser, "nurse", "/nurse");
  const kiosk = await kioskPage(browser, "normal");
  const { sessionId, patientToken } = await checkIn(kiosk, "100002");
  const alertsSeen = watchAlerts(nurse, sessionId, ["FAST_STROKE"]);
  const turns = await answerIntake(kiosk, PATIENT);
  await measureVitals(kiosk);
  await expect(kiosk.getByTestId("token-code"), "no token before the nurse decides").toHaveCount(0);

  // The alert stands on the patient's own answers.
  const http = await request.newContext({ baseURL: STACK.api });
  try {
    const nurseHeaders = { Authorization: `Bearer ${await staffTokenFor("nurse")}` };
    const facts = (await (await http.get(`/v1/sessions/${sessionId}/facts`, { headers: nurseHeaders })).json()) as {
      fast?: Record<string, Fact | undefined>;
    };
    const signs = ["face_droop", "arm_weakness", "speech_difficulty"].map((key) => facts.fast?.[key]);
    for (const sign of signs) expect(sign).toMatchObject({ provenance: "PATIENT_STATED", source: { kind: "transcript_span" } });
    expect(signs.filter((sign) => sign?.value === "yes")).toHaveLength(3);

    await assertJourney(
      { sessionId, nurse, patient: kiosk, patientToken, kioskToken: await kioskDeviceToken(kiosk), triggers: answerTriggers(turns), alertsSeen },
      { alerts: ["FAST_STROKE"], decision: { override: "suggestion", reason: "Clinical judgement" } },
    );

    // Clinic isolation: clinic B's nurse finds neither the patient nor the alert.
    const clinicB = { Authorization: `Bearer ${await staffTokenFor("nurse", "b")}` };
    const worklist = (await (await http.get("/v1/nurse/worklist", { headers: clinicB })).json()) as { session_id: string }[];
    expect(worklist.map((row) => row.session_id)).not.toContain(sessionId);
    expect((await http.get(`/v1/sessions/${sessionId}/triage`, { headers: clinicB })).status()).toBe(404);
    expect((await http.get(`/v1/sessions/${sessionId}/facts`, { headers: clinicB })).status()).toBe(404);
  } finally {
    await http.dispose();
  }
});
