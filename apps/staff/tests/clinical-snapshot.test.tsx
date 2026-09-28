import type { Schema } from "@careflow/contracts";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { AlertBanner } from "@/components/nurse/AlertBanner";
import { ClinicalSnapshot } from "@/components/nurse/ClinicalSnapshot";
import { TriageCard } from "@/components/nurse/TriageCard";
import { MESSAGES } from "@/i18n/messages";
import { indexFacts } from "@/lib/facts";

vi.mock("@/lib/api", () => ({ api: { POST: vi.fn() } }));

type StructuredFacts = Schema<"StructuredFacts">;
const WHEN = "2026-09-29T09:00:00Z";
const BASE: StructuredFacts = {
  session_id: "ses_snapshot", clinic_id: "clinic_demo", language: "en", extraction_status: "complete",
};
const stated = (factId: string, value: string) => ({
  fact_id: factId, value, provenance: "PATIENT_STATED" as const, recorded_at: WHEN,
  source: { kind: "transcript_span" as const, turn_id: "turn_snapshot", start: 0, end: value.length, quote: value },
});
const ehr = (factId: string, display: string, resourceType: string, id: string) => ({
  fact_id: factId, provenance: "EHR" as const, recorded_at: WHEN,
  value: { system: "https://example.test/codes", code: id, display },
  source: { kind: "fhir" as const, resource_type: resourceType, id, version_id: "3" },
});
const FACTS: StructuredFacts = {
  ...BASE,
  chief_complaint: stated("f_chief", "My heart has been racing."),
  symptom_onset_at: stated("f_onset", "since yesterday evening"),
  pain_score: { ...stated("f_pain", "no pain"), value: 0 },
  complaint_category: {
    fact_id: "f_category", provenance: "MODEL_INFERRED", recorded_at: WHEN,
    value: { system: "https://example.test/codes", code: "palpitations", display: "Palpitations" },
    source: { kind: "model", model_id: "demo-category-model", model_version: "2.7" },
  },
  chest_pain: {
    sweating: {
      fact_id: "f_sweating", value: "yes", provenance: "MODEL_INFERRED", recorded_at: WHEN,
      source: { kind: "model", model_id: "demo-sign-model", model_version: "2.7" },
    },
  },
  vitals: {
    heart_rate: {
      fact_id: "f_pulse", value: 112, unit: "/min", provenance: "DEVICE_MEASURED", recorded_at: WHEN,
      source: { kind: "device", device_id: "simulated_monitor_07", reading_id: "reading_112" },
    },
    acvpu: {
      fact_id: "f_awake", provenance: "NURSE_CONFIRMED", recorded_at: WHEN,
      value: { system: "https://example.test/acvpu", code: "A", display: "Alert" },
      source: { kind: "nurse", staff_user_id: "nurse_observer_3" },
    },
  },
  ehr_context: {
    allergies: [{ ...ehr("f_allergy", "Penicillin", "AllergyIntolerance", "allergy_penicillin"), criticality: "high" }],
    conditions: [ehr("f_condition", "Hypertension", "Condition", "condition_htn")],
    medications: [ehr("f_medication", "Metformin", "MedicationStatement", "medication_metformin")],
  },
};

function show(children: ReactNode) {
  return render(<NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC">{children}</NextIntlClientProvider>);
}

function card(title: string) {
  const section = screen.getByRole("heading", { name: title }).closest("section");
  expect(section).not.toBeNull();
  return section!;
}

describe("the sourced clinical snapshot", () => {
  it("shows onset, pulse, nurse observations and EHR history in their clinical groups", () => {
    show(<ClinicalSnapshot facts={FACTS} neededFacts={[]} />);

    const story = card("Patient story");
    expect(story).toHaveTextContent("My heart has been racing.");
    expect(story).toHaveTextContent("since yesterday evening");
    expect(story.querySelector('[data-provenance="PATIENT_STATED"]')).toBeInTheDocument();
    const observations = card("Observations and vitals");
    expect(observations).toHaveTextContent("112 /min");
    expect(observations).toHaveTextContent("Alert");
    expect(observations.querySelector('[data-provenance="DEVICE_MEASURED"]')).toBeInTheDocument();
    expect(observations.querySelector('[data-provenance="NURSE_CONFIRMED"]')).toBeInTheDocument();
    const history = card("History and medicines");
    for (const text of ["Penicillin", "Hypertension", "Metformin"]) {
      expect(history).toHaveTextContent(text);
    }
    expect(history.querySelectorAll('[data-provenance="EHR"]')).toHaveLength(3);
    expect(history).toHaveTextContent(/criticality.*high/i);
  });

  it("exposes the actual source details without presenting simulated readings as camera findings", async () => {
    const { container } = show(<ClinicalSnapshot facts={FACTS} neededFacts={[]} />);
    const expanders = screen.getAllByText("Source details", { selector: "summary" });
    for (const expander of expanders) {
      await userEvent.click(expander);
      expect(expander.closest("details")).toHaveAttribute("open");
    }

    for (const text of ["simulated_monitor_07", "reading_112", "nurse_observer_3", "condition_htn", "medication_metformin", "allergy_penicillin", "demo-sign-model", "2.7"]) {
      expect(container).toHaveTextContent(text);
    }
    expect(container).toHaveTextContent("My heart has been racing.");
    expect(container.querySelector('[data-provenance="MODEL_INFERRED"]')).toBeInTheDocument();
    expect(container).not.toHaveTextContent(/camera (?:measured|detected|observed)/i);
  });

  it("marks absent observations as not recorded, without converting absence to normal values", () => {
    const { container } = show(<ClinicalSnapshot facts={BASE} neededFacts={[]} />);

    expect(screen.getAllByText("Not recorded").length).toBeGreaterThanOrEqual(3);
    expect(card("Patient story")).toHaveTextContent("Not recorded");
    expect(card("Observations and vitals")).toHaveTextContent("Not recorded");
    expect(screen.queryByText(/^normal$/i)).not.toBeInTheDocument();
    expect(container).not.toHaveTextContent(/\b0\s*\/min\b|\b0%\b/i);
    expect(container.querySelectorAll("[data-provenance]")).toHaveLength(0);
  });

  it("preserves onset facts recorded at the main, chest-pain and individual symptom paths", () => {
    const facts: StructuredFacts = {
      ...BASE,
      symptom_onset_at: stated("f_main_onset", "since last evening"),
      chest_pain: { onset: stated("f_chest_onset", "for two hours") },
      symptoms: [{ name: stated("f_cough", "cough"), onset: stated("f_cough_onset", "for three days") }],
    };
    show(<ClinicalSnapshot facts={facts} neededFacts={[]} />);

    const story = card("Patient story");
    for (const text of ["since last evening", "for two hours", "for three days", "cough"]) {
      expect(story).toHaveTextContent(text);
    }
    expect(story.querySelectorAll('[data-provenance="PATIENT_STATED"]')).toHaveLength(4);
  });

  it("preserves a recorded zero pain score instead of treating it as a missing answer", () => {
    const { container } = show(<ClinicalSnapshot facts={FACTS} neededFacts={[]} />);
    const pain = container.querySelector('[data-field="pain_score"]') as HTMLElement;

    expect(within(pain).getByText("0")).toBeInTheDocument();
    expect(pain).not.toHaveTextContent("Not recorded");
    expect(pain.querySelector('[data-provenance="PATIENT_STATED"]')).toBeInTheDocument();
  });

  it("shows the outstanding questions and distinguishes nurse inputs from patient answers", () => {
    show(<ClinicalSnapshot facts={BASE} neededFacts={[
      { key: "vitals.respiratory_rate", audience: "nurse", reason: "news2_input" },
      { key: "symptom_onset_at", audience: "patient", reason: "intake_input" },
    ]} />);

    const respiratory = screen.getByText("respiratory rate").closest("li");
    const onset = screen.getByText("symptom onset at").closest("li");
    expect(respiratory).toHaveTextContent(/nurse/i);
    expect(onset).toHaveTextContent(/patient/i);
    expect(respiratory?.querySelector("[data-provenance]")).toBeNull();
    expect(onset?.querySelector("[data-provenance]")).toBeNull();
  });

  it("distinguishes unavailable clinical data and empty EHR lists from a negative medical history", () => {
    const { rerender, container } = show(<ClinicalSnapshot facts={null} neededFacts={[]} />);
    expect(screen.getByText(/Clinical details could not be loaded/)).toBeInTheDocument();
    expect(container).not.toHaveTextContent(/no allergies|no medical history/i);

    rerender(<NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC"><ClinicalSnapshot facts={{ ...BASE, ehr_context: { allergies: [], conditions: [], medications: [] } }} neededFacts={[]} /></NextIntlClientProvider>);
    const history = card("History and medicines");
    expect(within(history).getAllByText(/No sourced/).length).toBeGreaterThanOrEqual(1);
    expect(history).not.toHaveTextContent(/no allergies|no medical history/i);
  });
});

describe("the model suggestion's supporting evidence", () => {
  it("shows the actual model identity and source-backed reasons, marking missing references", () => {
    const triage: Schema<"TriageView"> = {
      session_id: BASE.session_id, status: "awaiting_nurse", alerts: [], needed_facts: [],
      suggestion: {
        suggestion_id: "suggestion_snapshot", session_id: BASE.session_id,
        level: 2, scale: "KTAS", confidence: 0.71,
        model_id: "careflow-test-urgency-model", model_version: "7.4.1", created_at: WHEN,
        reasons: [
          { text: "Reported palpitations", fact_ids: ["f_chief"] },
          { text: "Elevated pulse", fact_ids: ["f_pulse"] },
          { text: "Unresolved observation", fact_ids: ["f_missing"] },
        ],
      },
    };
    show(<TriageCard triage={triage} facts={indexFacts(FACTS)} warnings={[]} nurseUserId="nurse_demo" onDecided={vi.fn()} />);

    const suggestion = screen.getByRole("region", { name: "Model suggestion" });
    expect(suggestion).toHaveTextContent("careflow-test-urgency-model");
    expect(suggestion).toHaveTextContent("7.4.1");
    expect(suggestion).toHaveTextContent("Suggestion only. You decide.");
    const reason = (text: string) => within(suggestion).getByText(text).closest("li")!;
    expect(reason("Reported palpitations")).toHaveTextContent("My heart has been racing.");
    expect(reason("Reported palpitations").querySelector('[data-provenance="PATIENT_STATED"]')).toBeInTheDocument();
    expect(reason("Elevated pulse")).toHaveTextContent("112 /min");
    expect(reason("Elevated pulse").querySelector('[data-provenance="DEVICE_MEASURED"]')).toBeInTheDocument();
    expect(reason("Unresolved observation")).toHaveTextContent("Source missing");
    expect(screen.queryByTestId("decision-result")).not.toBeInTheDocument();
  });
});


describe("safety alert evidence", () => {
  it("keeps the actual rule code visible and flags partially missing supporting facts", () => {
    show(<AlertBanner alert={{
      alert_id: "alr_missing", session_id: BASE.session_id, rule_id: "FAST_STROKE",
      ruleset_version: "2026.1.0", severity: "critical", evidence_fact_ids: ["f_chief", "f_missing"],
      required_action: "urgent_review", partial: false, missing_inputs: [], raised_at: WHEN,
    }} facts={indexFacts(FACTS)} tasks={[]} />);
    expect(screen.getByTestId("alert-banner")).toHaveTextContent("Safety rule: FAST_STROKE · ruleset 2026.1.0");
    expect(screen.getByTestId("alert-evidence")).toHaveTextContent("My heart has been racing.");
    expect(screen.getByTestId("alert-evidence-incomplete")).toHaveTextContent("1 supporting fact(s) could not be loaded");
    expect(screen.getByTestId("alert-banner")).toHaveTextContent("Critical");
  });
});
