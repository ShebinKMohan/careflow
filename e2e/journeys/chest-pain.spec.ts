import { expect, test } from "@playwright/test";

import { answerIntake, checkIn, closeToken, kioskDeviceToken, kioskPage, measureVitals, type PatientScript, staffPage } from "../lib/actors";
import { codes, Hapi } from "../lib/fhir";
import type { Locale } from "../lib/i18n";
import {
  answerTriggers,
  assertAlerts,
  assertBriefing,
  assertDecision,
  assertTokenAndRoom,
  assertWriteBack,
  type JourneyContext,
  watchAlerts,
} from "../lib/journey";
import { CHEST_PAIN, CHEST_PAIN_AR } from "../lib/scripts";

// Journey 1 (M1-19, M2-12): chest pain, persona `chest-pain` (keypad 100001, synthetic), in English
// and fully in Arabic (@ar: the kiosk in Arabic, the nurse and the doctor in English). The
// CHEST_PAIN_ECG_10MIN alert must reach the nurse before any level exists; the nurse records the
// ECG and decides; the kiosk shows the token; the doctor sees the patient's words with their
// source; HAPI holds the write-back once, with the ECG Task.
const SCRIPT: Record<Locale, PatientScript> = { en: CHEST_PAIN, ar: CHEST_PAIN_AR };
const COMPLAINT_ITEM: Record<Locale, RegExp> = { en: /chest pain/i, ar: /صدر/ };

for (const locale of ["en", "ar"] as const) {
  test(`${locale === "ar" ? "@ar " : ""}chest pain: the ECG alert reaches the nurse first, ECG done, the nurse decides, token, briefing, HAPI (${locale})`, async ({ browser }) => {
    const nurse = await staffPage(browser, "nurse", "/nurse", true); // through the login page
    const kiosk = await kioskPage(browser, "normal");
    const { sessionId, patientToken } = await checkIn(kiosk, "100001", locale);
    const alertsSeen = watchAlerts(nurse, sessionId, ["CHEST_PAIN_ECG_10MIN"]);
    const turns = await answerIntake(kiosk, SCRIPT[locale], { locale });
    const ctx: JourneyContext = {
      sessionId,
      nurse,
      patient: kiosk,
      patientToken,
      kioskToken: await kioskDeviceToken(kiosk),
      triggers: answerTriggers(turns),
      alertsSeen,
    };
    await assertAlerts(ctx, ["CHEST_PAIN_ECG_10MIN"]);

    // Safety rule 3: no level and no token before the nurse decides.
    await measureVitals(kiosk, locale);
    await expect(kiosk.getByTestId("token-code")).toHaveCount(0);

    // The ECG, recorded on the card: its countdown stops.
    await nurse.goto(`/nurse/${sessionId}`);
    await expect(nurse.locator('[data-testid="alert-banner"][data-rule-id="CHEST_PAIN_ECG_10MIN"]')).toContainText("Chest pain: ECG in 10 min");
    const ecg = nurse.getByTestId("ecg-status");
    await expect(ecg).toHaveText(/\d\d:\d\d/);
    await nurse.getByRole("button", { name: "ECG done" }).click();
    await expect(ecg).toHaveText(/^ECG done /);
    const stopped = await ecg.textContent();
    await nurse.waitForTimeout(2_000); // pacing only, after the element is visible: the countdown must not move
    await expect(ecg).toHaveText(stopped ?? "");

    const level = await assertDecision(ctx, ["CHEST_PAIN_ECG_10MIN"], { confirm: "suggestion", fallback: 2 });
    const kioskToken = kiosk.getByTestId("token-code");
    await expect(kioskToken).toHaveText(/^[A-Z]-\d{3}$/, { timeout: 30_000 });
    const token = (await kioskToken.textContent()) ?? "";
    await expect(nurse.getByTestId("decision-result")).toContainText(`Number ${token}`); // one queue assignment, the same everywhere
    const called = await assertTokenAndRoom(ctx);
    expect(called.token_code).toBe(token);

    // The doctor sees the patient's own words behind the complaint.
    const doctor = await staffPage(browser, "doctor", `/doctor/${sessionId}`);
    const briefing = doctor.getByTestId("briefing");
    await expect(briefing).toBeVisible({ timeout: 30_000 });
    const complaint = briefing.getByRole("listitem").filter({ hasText: COMPLAINT_ITEM[locale] }).getByRole("button", { name: /Patient said/ }).first();
    await complaint.click();
    const mark = doctor.getByRole("dialog", { name: "Source" }).locator("mark");
    await expect(mark).toBeVisible();
    const quote = ((await mark.textContent()) ?? "").trim();
    expect(quote.length).toBeGreaterThan(0);
    expect(turns.some((turn) => turn.answer.includes(quote)), `"${quote}" is the patient's own words`).toBe(true);
    await assertBriefing(ctx);
    await assertWriteBack(ctx, ["CHEST_PAIN_ECG_10MIN"]);

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
    } finally {
      await hapi.close();
    }
    await closeToken(nurse, called.token_id); // seen: the room is free for the next run
  });
}
