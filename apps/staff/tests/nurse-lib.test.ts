import type { Schema } from "@careflow/contracts";
import { describe, expect, it } from "vitest";

import { alertText } from "@/lib/alerts";
import { countdown } from "@/lib/countdown";
import { decisionBody, NO_SUGGESTION_NOTE, reasonGiven } from "@/lib/decision";
import { factLabel } from "@/lib/factLabel";
import { indexFacts } from "@/lib/facts";
import { sortWorklist, type WorklistItem } from "@/lib/sortWorklist";

function item(id: string, extra: Partial<WorklistItem> = {}): WorklistItem {
  return { session_id: id, patient_display: id, arrived_at: "2026-09-26T09:00:00Z", state: "awaiting_nurse", alerts: [], open_tasks: [], ...extra };
}
const chip = (severity: "critical" | "high") => ({ alert_id: `a_${severity}`, rule_id: "SEPSIS_NEWS2" as const, severity, partial: false });

describe("sortWorklist", () => {
  it("puts active critical, then high alerts first, then the level, then the arrival", () => {
    const items = [
      item("late_l1", { suggested_level: 1, arrived_at: "2026-09-26T09:30:00Z" }),
      item("early_l1", { suggested_level: 1, arrived_at: "2026-09-26T09:10:00Z" }),
      item("high", { suggested_level: 4, alerts: [chip("high")] }),
      item("critical", { suggested_level: 5, alerts: [chip("critical")] }),
      item("none"),
      item("l3", { suggested_level: 3 }),
    ];
    expect(sortWorklist(items).map((i) => i.session_id)).toEqual(["critical", "high", "early_l1", "late_l1", "none", "l3"]);
  });

  it("treats no suggestion as level 2 and stops ranking the alerts of a confirmed session", () => {
    const items = [
      item("l2", { suggested_level: 2, arrived_at: "2026-09-26T09:05:00Z" }),
      item("none", { arrived_at: "2026-09-26T09:01:00Z" }),
      item("confirmed", { confirmed_level: 4, alerts: [chip("critical")] }),
    ];
    expect(sortWorklist(items).map((i) => i.session_id)).toEqual(["none", "l2", "confirmed"]);
  });
});

describe("countdown", () => {
  const deadline = Date.parse("2026-09-26T09:10:00Z");
  it("counts down in mm:ss, red under 2 minutes", () => {
    expect(countdown(deadline, deadline - 9 * 60_000)).toEqual({ time: "09:00", overdue: false, urgent: false });
    expect(countdown(deadline, deadline - 60_000)).toEqual({ time: "01:00", overdue: false, urgent: true });
    expect(countdown(deadline, deadline - 500)).toEqual({ time: "00:01", overdue: false, urgent: true });
  });
  it("then counts up how long it is overdue", () => {
    expect(countdown(deadline, deadline + 60_000)).toEqual({ time: "01:00", overdue: true, urgent: true });
    expect(countdown(deadline, deadline + 61_900)).toEqual({ time: "01:01", overdue: true, urgent: true });
    expect(countdown(deadline, deadline + (16 * 3600 + 50 * 60 + 17) * 1000)).toMatchObject({ time: "16:50:17" });
  });
});

describe("decisionBody", () => {
  const suggestion: Schema<"UrgencySuggestion"> = {
    suggestion_id: "sug_1", session_id: "ses_1", level: 2, scale: "KTAS", confidence: 0.7,
    reasons: [{ text: "x", fact_ids: [] }], model_id: "m", model_version: "0.1.0", created_at: "2026-09-26T09:00:00Z",
  };
  const base = { sessionId: "ses_1", nurseUserId: "nurse_1", alertIds: ["alr_1"], scale: "KTAS" as const, decisionId: "dec_1", decidedAt: "2026-09-26T09:06:00Z" };

  it("confirms the suggested level", () => {
    expect(decisionBody({ ...base, suggestion, level: 2, override: null })).toEqual({
      decision_id: "dec_1", session_id: "ses_1", suggestion_id: "sug_1", action: "CONFIRM", final_level: 2, scale: "KTAS",
      nurse_user_id: "nurse_1", acknowledged_alert_ids: ["alr_1"], decided_at: "2026-09-26T09:06:00Z",
    });
  });

  it("refuses to confirm another level, or an override without a reason", () => {
    expect(() => decisionBody({ ...base, suggestion, level: 1, override: null })).toThrow();
    expect(() => decisionBody({ ...base, suggestion, level: 1, override: { code: null, note: "abc" } })).toThrow();
  });

  it("overrides with a reason code, and free text of 5+ characters counts as 'other'", () => {
    const coded = decisionBody({ ...base, suggestion, level: 1, override: { code: "patient_deteriorated", note: "" } });
    expect(coded).toMatchObject({ action: "OVERRIDE", final_level: 1, override_reason_code: "patient_deteriorated" });
    expect(coded).not.toHaveProperty("override_note");
    const typed = decisionBody({ ...base, suggestion, level: 3, override: { code: null, note: "  looks worse  " } });
    expect(typed).toMatchObject({ action: "OVERRIDE", override_reason_code: "other", override_note: "looks worse" });
  });

  it("with no suggestion the nurse's level goes as an override with the pre-filled reason", () => {
    const body = decisionBody({ ...base, suggestion: null, level: 2, override: { code: "other", note: NO_SUGGESTION_NOTE } });
    expect(body).toMatchObject({ suggestion_id: null, action: "OVERRIDE", override_reason_code: "other", override_note: NO_SUGGESTION_NOTE, scale: "KTAS" });
  });

  it("knows when a reason is given", () => {
    expect(reasonGiven({ code: null, note: "" })).toBe(false);
    expect(reasonGiven({ code: "other", note: "abcd" })).toBe(false);
    expect(reasonGiven({ code: "other", note: "abcde" })).toBe(true);
    expect(reasonGiven({ code: "clinical_judgement", note: "" })).toBe(true);
  });
});

describe("facts", () => {
  it("indexes every fact by id with a readable value and its provenance", () => {
    const facts = {
      session_id: "ses_1",
      vitals: {
        heart_rate: { fact_id: "f_hr", provenance: "DEVICE_MEASURED", source: { kind: "device" }, recorded_at: "t", value: 125, unit: "/min" },
      },
      patient: { sex: { fact_id: "f_sex", provenance: "EHR", source: { kind: "fhir" }, recorded_at: "t", value: { system: "s", code: "male", display: "Male" } } },
      symptoms: [{ name: { fact_id: "f_sym", provenance: "PATIENT_STATED", source: { kind: "transcript" }, recorded_at: "t", value: "rigors" } }],
    } as unknown as Schema<"StructuredFacts">;
    const index = indexFacts(facts);
    expect(index.get("f_hr")).toEqual({ factId: "f_hr", path: "vitals.heart_rate", value: "125 /min", provenance: "DEVICE_MEASURED" });
    expect(factLabel("vitals.temperature_c")).toBe("temperature");
    expect(factLabel("vitals.spo2")).toBe("SpO2");
    expect(factLabel("patient.age_years")).toBe("age");
    expect(factLabel("chest_pain.onset")).toBe("chest pain onset");
    expect(index.get("f_sex")?.value).toBe("Male");
    expect(index.get("f_sym")?.path).toBe("symptoms.0.name");
    expect(factLabel("vitals.heart_rate")).toBe("heart rate");
    expect(factLabel("symptoms.0.name")).toBe("symptoms name");
    expect(indexFacts(null).size).toBe(0);
  });
});

describe("alertText", () => {
  it("uses display_text, else the rule's fixed template", () => {
    const alert = {
      alert_id: "a", session_id: "s", rule_id: "CHEST_PAIN_ECG_10MIN", ruleset_version: "1", severity: "critical",
      evidence_fact_ids: [], required_action: "ecg_within_10_min", partial: false, missing_inputs: [], raised_at: "t",
    } as Schema<"Alert">;
    expect(alertText(alert, "en")).toBe("Chest pain: ECG within 10 minutes of arrival.");
    expect(alertText({ ...alert, display_text: "Custom line." }, "en")).toBe("Custom line.");
    expect(alertText(alert, "ar")).toContain("تخطيط القلب");
  });
});
