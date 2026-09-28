import { expect, test } from "@playwright/test";

import { answerIntake, checkIn, closeToken, kioskDeviceToken, kioskPage, type PatientScript, staffPage, staffToken } from "../lib/actors";
import { type Locale, msg } from "../lib/i18n";
import { answerTriggers, assertAlerts, assertBriefing, assertDecision, assertTokenAndRoom, assertWriteBack, type JourneyContext } from "../lib/journey";
import { STACK } from "../lib/stack";

// @child (M2-04, SHE-293): a parent brings persona `child-drowsy` (keypad 100003, synthetic, 2
// years old) to the kiosk, in English and in Arabic. The parent answers "Who is the patient?" with
// "My child" and the age, types the complaint, then taps the child questions: "Yes" to hard to
// wake, "No" to the rest. The NICE NG143 red flag must reach the nurse's open worklist as a
// critical alert within 2 s of that tap, from the parent's own answer (PATIENT_STATED); the kiosk
// never shows a vitals screen; the adults-only urgency model gives no suggestion; and the nurse
// still sets the level. Taps need no model; the complaint's replies are in the cassette.
const HARD_TO_WAKE: Record<Locale, RegExp> = { en: /very hard to wake/, ar: /إيقاظ طفلك/ };
const PARENT: Record<Locale, PatientScript> = {
  en: { complaint: "My son has had a fever since last night", yes: [HARD_TO_WAKE.en], scale: "0", typed: [] },
  ar: { complaint: "ابني عنده حمّى منذ الليلة الماضية", yes: [HARD_TO_WAKE.ar], scale: "0", typed: [] },
};
const RED_FLAG_CHIP = `${msg("staff", "en", "safety.alerts.severity.critical")}: ${msg("staff", "en", "safety.alerts.rules.NG143_RED_FLAG")}`;

interface Fact {
  value: string;
  provenance: string;
  source: { quote?: string };
}

for (const locale of ["en", "ar"] as const) {
  test(`@child${locale === "ar" ? " @ar" : ""} a parent's "Yes" to hard to wake reaches the nurse as a critical alert, with no vitals step and no model suggestion (${locale})`, async ({
    browser,
  }) => {
    const t = (key: string) => msg("kiosk", locale, key);
    const nurse = await staffPage(browser, "nurse", "/nurse");
    const kiosk = await kioskPage(browser, "normal");
    const visited: string[] = [];
    kiosk.on("framenavigated", (frame) => {
      if (frame === kiosk.mainFrame()) visited.push(new URL(frame.url()).pathname);
    });
    const { sessionId, patientToken } = await checkIn(kiosk, "100003", locale, { years: 2 });

    // This session's row on the nurse's open worklist (earlier runs' children stay on it).
    const chip = nurse.locator(`tr[data-session="${sessionId}"]`).filter({ hasText: RED_FLAG_CHIP });
    const seen = chip.waitFor({ timeout: 120_000 }).then(() => Date.now());
    const turns = await answerIntake(kiosk, PARENT[locale], { limit: 30, locale });
    const seenAt = await seen;

    // The bank came right after the complaint, hard to wake first, answered by a tap.
    const [complaint, ...tapped] = turns;
    expect(complaint?.mode).toBe("text");
    expect(tapped.every((turn) => turn.mode === "tap"), "every child question is answered by a tap").toBe(true);
    const trigger = tapped[0];
    if (!trigger) throw new Error("no child question was asked after the complaint");
    expect(HARD_TO_WAKE[locale].test(trigger.question), `the first child question is hard to wake: "${trigger.question}"`).toBe(true);
    expect(trigger.answer).toBe(t("intake.answers.yes"));
    const afterAnswer = seenAt - trigger.answeredAt;
    test.info().annotations.push({
      type: "alert latency",
      description: `"${trigger.answer}" to "${trigger.question}": on the nurse's worklist ${seenAt - trigger.sentAt} ms after the tap (${afterAnswer} ms after the kiosk got the answer)`,
    });
    // A speed budget: asserted on the reference machine, reported on CI (docs/FLAKY.md).
    if (process.env.CI) {
      if (afterAnswer > 2_000) test.info().annotations.push({ type: "warning", description: `alert on the worklist ${afterAnswer} ms after the answer (budget 2000 ms)` });
    } else {
      expect(afterAnswer, "the nurse's worklist shows the red flag within 2 s of the tap").toBeLessThanOrEqual(2_000);
    }

    // The kiosk skips the adult sensor steps: it ends on the child's done screen.
    await expect(kiosk).toHaveURL(/\/done$/, { timeout: 30_000 });
    const told = kiosk.getByTestId("child-nurse");
    await expect(told).toContainText(t("safety.child.nurseTold")); // no diagnosis, no advice
    await expect(told).toContainText(t("safety.child.nurseCheck"));
    expect(visited.filter((path) => path.endsWith("/vitals")), "no vitals screen ever appears").toEqual([]);

    // The alert stands on the parent's own answer, and no model suggested a level for a child.
    const headers = { Authorization: `Bearer ${await staffToken(nurse)}` };
    const facts = await (await nurse.request.get(`${STACK.api}/v1/sessions/${sessionId}/facts`, { headers })).json();
    const drowsy = facts.child_danger?.hard_to_wake as Fact | undefined;
    expect(drowsy).toMatchObject({ value: "yes", provenance: "PATIENT_STATED", source: { quote: t("intake.answers.yes") } });
    const view = await (await nurse.request.get(`${STACK.api}/v1/sessions/${sessionId}/triage`, { headers })).json();
    const alerts = view.alerts as { rule_id: string; severity: string }[];
    expect(alerts).toContainEqual(expect.objectContaining({ rule_id: "NG143_RED_FLAG", severity: "critical" }));
    expect(view.suggestion ?? null, "the adults-only urgency model gives a child no suggestion").toBeNull();

    // Safety rule 3: no token until the nurse sets the level, on the card that shows the alert.
    await expect(kiosk.getByTestId("token-code")).toHaveCount(0);
    await nurse.goto(`/nurse/${sessionId}`);
    await expect(nurse.getByTestId("alert-banner").filter({ hasText: msg("staff", "en", "safety.alerts.rules.NG143_RED_FLAG") })).toBeVisible();
    await expect(nurse.getByText("No model suggestion").first()).toBeVisible();
    // b to e: the nurse sets the level; token and room; the attributed briefing; HAPI. A child
    // skips the sensors, so the write-back need not hold an Observation.
    const ctx: JourneyContext = {
      sessionId,
      nurse,
      patient: kiosk,
      patientToken,
      kioskToken: await kioskDeviceToken(kiosk),
      triggers: answerTriggers(turns),
      alertsSeen: new Map([["NG143_RED_FLAG", Promise.resolve(seenAt)]]),
    };
    await assertAlerts(ctx, ["NG143_RED_FLAG"]);
    await assertDecision(ctx, ["NG143_RED_FLAG"], { set: 2 });
    await expect(kiosk.getByTestId("token-code")).toHaveText(/^[A-Z]-\d{3}$/, { timeout: 30_000 });
    const called = await assertTokenAndRoom(ctx);
    await assertBriefing(ctx);
    await assertWriteBack(ctx, ["NG143_RED_FLAG"], { observations: false });
    await closeToken(nurse, called.token_id); // seen: the room is free for the next run
  });
}

// Consent comes before "Who is the patient?" (ADR-036): a parent who does not agree to data use is
// sent to reception, and the kiosk checks nothing in, so nothing reaches the clinic record.
for (const locale of ["en", "ar"] as const) {
  test(`@child${locale === "ar" ? " @ar" : ""} a parent who declines data use is sent to reception, and nothing is checked in (${locale})`, async ({ browser }) => {
    const t = (key: string) => msg("kiosk", locale, key);
    const kiosk = await kioskPage(browser, "normal");
    const sent: string[] = [];
    kiosk.on("request", (request) => {
      const url = new URL(request.url());
      if (url.origin === new URL(STACK.api).origin) sent.push(`${request.method()} ${url.pathname}`);
    });
    await kiosk.goto("/");
    await kiosk.getByRole("main").getByRole("button", { name: msg("kiosk", "en", locale === "en" ? "home.english" : "home.arabic") }).click();
    await expect(kiosk).toHaveURL(/\/consent$/);
    await kiosk.getByRole("button", { name: t("safety.consent.decline"), exact: true }).click();
    await expect(kiosk.getByTestId("consent-declined")).toHaveText(t("safety.consent.reception"));
    await expect(kiosk.getByRole("button", { name: t("safety.consent.agree"), exact: true })).toHaveCount(0);
    await kiosk.getByRole("button", { name: t("safety.consent.startOver"), exact: true }).click();
    await expect(kiosk).toHaveURL(/\/$/);
    expect(sent.filter((call) => /\/v1\/(checkin|sessions)/.test(call)), "nothing is checked in").toEqual([]);
  });
}
