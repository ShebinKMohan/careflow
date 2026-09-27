import { expect, test } from "@playwright/test";

import { answerIntake, checkIn, kioskPage, measureVitals, nurseDecides, type PatientScript, staffPage } from "../lib/actors";
import { Hapi, NEWS2_TOTAL, codes } from "../lib/fhir";

// Journey 4 (M1-19): sepsis, persona `sepsis` (keypad 100004, synthetic), with the simulated
// device on its septic scenario. The kiosk's partial NEWS2 already raises SEPSIS_NEWS2; the
// nurse's measurements complete the NEWS2; the confirmed triage writes the full NEWS2 and a Flag.
const PATIENT: PatientScript = {
  complaint: "I've had a fever and shivering since yesterday",
  yes: [/fever/i, /shaking or shivering/i],
  scale: "5",
  typed: [[/when did (this|the) .*start/i, "Since yesterday evening"]],
};

test("sepsis: partial NEWS2 alert from the kiosk, the nurse completes NEWS2 and decides, HAPI has NEWS2 and a Flag", async ({ browser }) => {
  const kiosk = await kioskPage(browser, "septic");
  const { sessionId } = await checkIn(kiosk, "100004");
  await answerIntake(kiosk, PATIENT);
  await measureVitals(kiosk);

  const nurse = await staffPage(browser, "nurse", `/nurse/${sessionId}`);
  const banner = nurse.getByTestId("alert-banner").filter({ hasText: "Possible sepsis (NEWS2)" });
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

  const level = await nurseDecides(nurse, 2);
  await expect(nurse.getByTestId("decision-result")).toContainText(`KTAS ${level}`);
  await expect(kiosk.getByTestId("token-code")).toHaveText(/^[A-Z]-\d{3}$/, { timeout: 30_000 });

  const hapi = await Hapi.open();
  try {
    const encounter = await hapi.encounter(sessionId);
    const id = String(encounter.id);
    const news2 = (await hapi.forEncounter("Observation", id)).filter((obs) => codes(obs, "code").includes(NEWS2_TOTAL));
    expect(news2, "one NEWS2 total").toHaveLength(1);
    expect((news2[0] as { status: string }).status, "the nurse completed it: final, not preliminary").toBe("final");
    expect((await hapi.forEncounter("Flag", id)).length).toBeGreaterThanOrEqual(1);
  } finally {
    await hapi.close();
  }
});
