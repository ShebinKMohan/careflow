import type { Schema } from "@careflow/contracts";

export type NurseDecision = Schema<"NurseDecision">;
export type UrgencySuggestion = Schema<"UrgencySuggestion">;
export type OverrideReasonCode = NonNullable<NurseDecision["override_reason_code"]>;
export type TriageScale = Schema<"TriageScale">;

export const OVERRIDE_REASONS: readonly OverrideReasonCode[] = [
  "clinical_judgement",
  "new_information",
  "patient_deteriorated",
  "model_error",
  "other",
];

/** Free text alone is a reason once it has this many characters (sent as code `other`). */
export const MIN_REASON_NOTE = 5;

/** The note pre-filled when the model gave no suggestion: the nurse sets the level alone. */
export const NO_SUGGESTION_NOTE = "no model suggestion";

export interface OverrideReason {
  code: OverrideReasonCode | null;
  note: string;
}

/** A reason is given when a code is picked (with a note for "other") or the note is long enough. */
export function reasonGiven(reason: OverrideReason): boolean {
  const note = reason.note.trim();
  if (reason.code && reason.code !== "other") return true;
  return note.length >= MIN_REASON_NOTE;
}

export interface DecisionInput {
  sessionId: string;
  suggestion: UrgencySuggestion | null;
  /** 1 (most urgent) to 5. */
  level: number;
  /** Null for "Confirm level N" (the suggested level); a reason for any other decision. */
  override: OverrideReason | null;
  nurseUserId: string;
  alertIds: string[];
  scale: TriageScale;
  decisionId: string;
  decidedAt: string;
}

/**
 * The body of `POST /v1/nurse/sessions/{id}/decision` (the contract's NurseDecision): CONFIRM
 * takes the suggested level; anything else is an OVERRIDE with a reason code (safety rule 3).
 */
export function decisionBody(input: DecisionInput): NurseDecision {
  const base = {
    decision_id: input.decisionId,
    session_id: input.sessionId,
    suggestion_id: input.suggestion?.suggestion_id ?? null,
    final_level: input.level,
    scale: input.suggestion?.scale ?? input.scale,
    nurse_user_id: input.nurseUserId,
    acknowledged_alert_ids: input.alertIds,
    decided_at: input.decidedAt,
  };
  if (input.override === null) {
    if (!input.suggestion || input.suggestion.level !== input.level) {
      throw new Error("Only the suggested level can be confirmed without a reason.");
    }
    return { ...base, action: "CONFIRM" };
  }
  if (!reasonGiven(input.override)) throw new Error("An override needs a reason.");
  const note = input.override.note.trim();
  return {
    ...base,
    action: "OVERRIDE",
    override_reason_code: input.override.code ?? "other",
    ...(note ? { override_note: note.slice(0, 500) } : {}),
  };
}
