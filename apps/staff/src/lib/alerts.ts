import type { Schema } from "@careflow/contracts";
import { ALERT_TEMPLATES } from "@careflow/contracts/alert-templates";

export type Alert = Schema<"Alert">;

/** The alert line: the engine's display_text, else the fixed template for its rule (never PHI). */
export function alertText(alert: Alert, locale: "en" | "ar"): string {
  return alert.display_text ?? ALERT_TEMPLATES[alert.rule_id][locale];
}

/** The ECG-within-10-minutes rule is the one with a countdown and an "ECG done" task. */
export const ECG_RULE = "CHEST_PAIN_ECG_10MIN";
export const ECG_TASK = "ecg_within_10_min";
