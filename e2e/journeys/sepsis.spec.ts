import { expect, test } from "@playwright/test";

import { answerIntake, checkIn, closeToken, kioskDeviceToken, kioskPage, measureVitals, type PatientScript, staffPage } from "../lib/actors";
import { codes, Hapi, NEWS2_TOTAL } from "../lib/fhir";
import {
  answerTriggers,
  assertAlerts,
  assertBriefing,
  assertDecision,
  assertTokenAndRoom,
  assertWriteBack,
  type JourneyContext,
  readingTriggers,
  watchAlerts,
} from "../lib/journey";
import { SEPSIS } from "../lib/scripts";

// Journey 4 (M1-19, M2-12): sepsis, persona `sepsis` (keypad 100004, synthetic), with the simulated
// device on its septic scenario. The kiosk's reading already raises SEPSIS_NEWS2 on a partial
// NEWS2, live on the nurse's worklist; the nurse's measurements complete the NEWS2; the nurse
// decides; the confirmed triage writes the full NEWS2 and a Flag.
const PATIENT: PatientScript = SEPSIS;

test("sepsis: partial NEWS2 alert from the kiosk, the nurse completes NEWS2 and decides, token, briefing, HAPI has NEWS2 and a Flag", async ({ browser }) => {
  const worklist = await staffPage(browser, "nurse", "/nurse");
  const kiosk = await kioskPage(browser, "septic");
  const { sessionId, patientToken } = await checkIn(kiosk, "100004");
  const alertsSeen = watchAlerts(worklist, sessionId, ["SEPSIS_NEWS2"]);
  const readings = readingTriggers(kiosk);
  const turns = await answerIntake(kiosk, PATIENT);
  await measureVitals(kiosk);
  const ctx: JourneyContext = {
    sessionId,
    nurse: worklist,
    patient: kiosk,
    patientToken,
    kioskToken: await kioskDeviceToken(kiosk),
    triggers: [...answerTriggers(turns), ...readings],
    alertsSeen,
  };
  await assertAlerts(ctx, ["SEPSIS_NEWS2"]);

  const nurse = await staffPage(browser, "nurse", `/nurse/${sessionId}`);
  const banner = nurse.locator('[data-testid="alert-banner"][data-rule-id="SEPSIS_NEWS2"]');
  await expect(banner).toBeVisible({ timeout: 60_000 });
  await expect(banner).toContainText("Partial NEWS2");
  await expect(nurse.getByTestId("news2-partial")).toBeVisible();

  await nurse.getByLabel("Respiratory rate (/min)").fill("24");
  await nurse.getByLabel("Systolic blood pressure (mmHg)").fill("95");
  await nurse.getByRole("button", { name: "Alert", exact: true }).click();
  await nurse.getByRole("group", { name: "On supplemental oxygen" }).getByRole("button", { name: "No" }).click();
  await nurse.getByRole("button", { name: "Save vitals" }).click();
  await expect(nurse.getByTestId("news2-summary")).toContainText(/NEWS2 \d+/);
  await expect(nurse.getByTestId("news2-partial")).toHaveCount(0);

  await assertDecision({ ...ctx, nurse }, ["SEPSIS_NEWS2"], { confirm: "suggestion", fallback: 2 });
  const called = await assertTokenAndRoom({ ...ctx, nurse });
  await assertBriefing(ctx);
  await assertWriteBack(ctx, ["SEPSIS_NEWS2"]);

  const hapi = await Hapi.open();
  try {
    const id = String((await hapi.encounter(sessionId)).id);
    const news2 = (await hapi.forEncounter("Observation", id)).filter((obs) => codes(obs, "code").includes(NEWS2_TOTAL));
    expect(news2, "one NEWS2 total").toHaveLength(1);
    expect((news2[0] as { status: string }).status, "the nurse completed it: final, not preliminary").toBe("final");
  } finally {
    await hapi.close();
  }
  await closeToken(ctx.nurse, called.token_id); // seen: the room is free for the next journey
});
