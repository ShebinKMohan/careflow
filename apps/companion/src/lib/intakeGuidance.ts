/** Input help follows the authoritative question key and never supplies an answer as a fact. */
export type GuidanceKey = "complaint" | "onset" | "location" | "scale" | "allergies" | "child" | "choice" | "general";

export function intakeGuidance(key: string | null): GuidanceKey {
  if (key === "chief_complaint") return "complaint";
  if (key === "symptom_onset_at" || key === "chest_pain.onset" || key === "fast.last_known_well") return "onset";
  if (key === "pain_location") return "location";
  if (key === "pain_score" || key === "severity") return "scale";
  if (key === "allergies_confirm") return "allergies";
  if (key?.startsWith("child_")) return "child";
  if (key?.startsWith("fast.") || key?.startsWith("chest_pain.") || key?.startsWith("infection.")) return "choice";
  return "general";
}
