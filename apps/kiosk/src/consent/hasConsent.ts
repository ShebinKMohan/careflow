/**
 * The patient's consent choices (M2-05). Any code that would use the camera or the microphone,
 * now or later (M3-05), calls `hasConsent("camera" | "voice")` first, and does nothing without it.
 * The choices live in memory only, for the current patient; the kiosk records them through the API
 * once check-in has created the session, and a wipe forgets them.
 */
export interface ConsentChoices {
  camera: boolean;
  voice: boolean;
  dataUse: boolean;
}

/** The version of the consent text on screen (messages `safety.consent.*`). */
export const CONSENT_VERSION = "2026-09-27.1";

let current: ConsentChoices | null = null;

export function setConsentChoices(choices: ConsentChoices | null): void {
  current = choices;
}

export function consentChoices(): ConsentChoices | null {
  return current;
}

export function hasConsent(kind: "camera" | "voice"): boolean {
  return current !== null && current.dataUse && current[kind];
}
