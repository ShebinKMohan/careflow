/**
 * The tap answers for a question, by its key (the M1-07 template keys, which are StructuredFacts
 * paths). A tap sends the answer's label, in the session language, as `input_mode: "tap"`.
 */
export type AnswerKey =
  | "yes"
  | "no"
  | "notSure"
  | "ok"
  | "chestPain"
  | "breathing"
  | "fever"
  | "injury"
  | "pain"
  | "other";

export type QuickAnswers =
  | { kind: "choices"; answers: readonly AnswerKey[] }
  | { kind: "scale"; min: 0; max: 10 }
  | { kind: "none" };

export const COMPLAINTS: readonly AnswerKey[] = ["chestPain", "breathing", "fever", "injury", "pain", "other"];
const YES_NO: readonly AnswerKey[] = ["yes", "no", "notSure"];
const SCALE_KEYS = new Set(["pain_score", "severity"]);
/** Answered in the patient's own words: a time or a place. */
const TEXT_ONLY = new Set(["symptom_onset_at", "pain_location", "fast.last_known_well", "chest_pain.onset"]);
const YES_NO_KEYS = new Set(["pain_present", "injury", "allergies_confirm"]);
const YES_NO_GROUPS = ["fast.", "chest_pain.", "infection.", "child_danger.", "child_amber."];

export function quickAnswers(key: string | null): QuickAnswers {
  if (key === null) return { kind: "choices", answers: ["ok"] }; // a message to acknowledge
  if (key === "chief_complaint") return { kind: "choices", answers: COMPLAINTS };
  if (SCALE_KEYS.has(key)) return { kind: "scale", min: 0, max: 10 };
  if (TEXT_ONLY.has(key)) return { kind: "none" };
  if (YES_NO_KEYS.has(key) || YES_NO_GROUPS.some((group) => key.startsWith(group))) {
    return { kind: "choices", answers: YES_NO };
  }
  return { kind: "none" };
}
