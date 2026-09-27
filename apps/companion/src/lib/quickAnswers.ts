/**
 * The tap answers for a question, by its key: the same choices as the kiosk
 * (apps/kiosk/src/lib/quickAnswers.ts), so the intake reads the same on both screens. A tap sends
 * the answer's label, in the session language, as `input_mode: "tap"`.
 */
export type AnswerKey = "yes" | "no" | "notSure" | "ok" | "chestPain" | "breathing" | "fever" | "injury" | "pain" | "other";

export type QuickAnswers =
  | { kind: "choices"; answers: readonly AnswerKey[] }
  | { kind: "scale"; min: 0; max: 10 }
  | { kind: "none" };

const COMPLAINTS: readonly AnswerKey[] = ["chestPain", "breathing", "fever", "injury", "pain", "other"];
const YES_NO: readonly AnswerKey[] = ["yes", "no", "notSure"];
const SCALE_KEYS = new Set(["pain_score", "severity"]);
const TEXT_ONLY = new Set(["symptom_onset_at", "pain_location", "fast.last_known_well", "chest_pain.onset"]);
const YES_NO_KEYS = new Set(["pain_present", "injury", "allergies_confirm"]);
const YES_NO_GROUPS = ["fast.", "chest_pain.", "infection.", "child_danger.", "child_amber."];

export function quickAnswers(key: string | null): QuickAnswers {
  if (key === null) return { kind: "choices", answers: ["ok"] };
  if (key === "chief_complaint") return { kind: "choices", answers: COMPLAINTS };
  if (SCALE_KEYS.has(key)) return { kind: "scale", min: 0, max: 10 };
  if (TEXT_ONLY.has(key)) return { kind: "none" };
  if (YES_NO_KEYS.has(key) || YES_NO_GROUPS.some((group) => key.startsWith(group))) {
    return { kind: "choices", answers: YES_NO };
  }
  return { kind: "none" };
}
