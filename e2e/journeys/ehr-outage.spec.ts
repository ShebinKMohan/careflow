import { execFileSync } from "node:child_process";

import { expect, request, test } from "@playwright/test";

import { answerIntake, checkIn, kioskDeviceToken, kioskPage, measureVitals, staffPage, staffTokenFor } from "../lib/actors";
import { codes, Hapi, NEWS2_TOTAL } from "../lib/fhir";
import { answerTriggers, assertAlerts, assertDecision, assertWriteBack, type JourneyContext, readingTriggers, watchAlerts } from "../lib/journey";
import { SEPSIS } from "../lib/scripts";
import { STACK } from "../lib/stack";

// The hospital system goes down mid-visit (M2-12; the sepsis journey's outage variant). Persona
// `sepsis` (keypad 100004, synthetic) checks in while HAPI FHIR answers; then HAPI is paused
// (`docker pause`, resumed in any case). The nurse still sees the sepsis alert, completes NEWS2 and
// decides; the admin status says HAPI does not answer; nothing reaches HAPI. Once HAPI is back, the
// write-back that failed is retried and lands once: one Encounter, the final NEWS2 and the Flag.
const HAPI_CONTAINER = `${STACK.name}-hapi-1`;

test("EHR outage: HAPI down after check-in, the nurse keeps the alert and decides; the write-back lands once HAPI is back", async ({ browser }) => {
  test.setTimeout(360_000);
  const worklist = await staffPage(browser, "nurse", "/nurse");
  const kiosk = await kioskPage(browser, "septic");
  const { sessionId, patientToken } = await checkIn(kiosk, "100004");
  const alertsSeen = watchAlerts(worklist, sessionId, ["SEPSIS_NEWS2"]);
  const readings = readingTriggers(kiosk);
  const turns = await answerIntake(kiosk, SEPSIS);
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

  const http = await request.newContext({ baseURL: STACK.api });
  const hapiStatus = async () => {
    const response = await http.get("/v1/admin/status", { headers: { Authorization: `Bearer ${await staffTokenFor("admin")}` } });
    const services = ((await response.json()) as { services: { name: string; status: string }[] }).services;
    return services.find((service) => service.name === "hapi-fhir")?.status;
  };
  execFileSync("docker", ["pause", HAPI_CONTAINER]);
  try {
    // The nurse works on without the hospital system: the alert, NEWS2 and the decision.
    const nurse = await staffPage(browser, "nurse", `/nurse/${sessionId}`);
    await expect(nurse.locator('[data-testid="alert-banner"][data-rule-id="SEPSIS_NEWS2"]')).toBeVisible();
    await nurse.getByLabel("Respiratory rate (/min)").fill("24");
    await nurse.getByLabel("Systolic blood pressure (mmHg)").fill("95");
    await nurse.getByRole("button", { name: "Alert", exact: true }).click();
    await nurse.getByRole("group", { name: "On supplemental oxygen" }).getByRole("button", { name: "No" }).click();
    await nurse.getByRole("button", { name: "Save vitals" }).click();
    await expect(nurse.getByTestId("news2-partial")).toHaveCount(0);
    await assertDecision({ ...ctx, nurse }, ["SEPSIS_NEWS2"], { confirm: "suggestion", fallback: 2 });
    await expect(kiosk.getByTestId("token-code"), "the patient still gets a token").toHaveText(/^[A-Z]-\d{3}$/, { timeout: 30_000 });
    // Said as it is: the admin status shows the hospital system down.
    await expect.poll(hapiStatus, { message: "the admin status says HAPI does not answer", timeout: 60_000 }).not.toBe("ok");
  } finally {
    execFileSync("docker", ["unpause", HAPI_CONTAINER]);
  }

  // Back: the failed write-back is retried and lands once, with the nurse's NEWS2 and the Flag.
  await expect.poll(hapiStatus, { message: "HAPI answers again", timeout: 60_000 }).toBe("ok");
  const hapi = await Hapi.open();
  try {
    await expect
      .poll(async () => (await hapi.search("Encounter", { identifier: `urn:careflow:session|${sessionId}` })).length, {
        message: "the write-back lands once HAPI is back",
        timeout: 180_000,
      })
      .toBe(1);
    await assertWriteBack(ctx, ["SEPSIS_NEWS2"]);
    const id = String((await hapi.encounter(sessionId)).id);
    const news2 = (await hapi.forEncounter("Observation", id)).filter((obs) => codes(obs, "code").includes(NEWS2_TOTAL));
    expect(news2, "one NEWS2 total, the nurse's final one").toHaveLength(1);
    expect((news2[0] as { status: string }).status).toBe("final");
  } finally {
    await hapi.close();
    await http.dispose();
  }
});
