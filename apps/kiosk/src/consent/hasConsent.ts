/**
 * The patient's consent choices (M2-05). Any code that would use the camera or the microphone,
 * now or later (M3-05), calls `hasConsent("camera" | "voice")` first, and does nothing without it.
 * The choices live in memory only, for the current patient; the kiosk records them through the API
 * once check-in has created the session, and a wipe forgets them. Choices count only once the API
 * has saved them (`markConsentSaved`): until then, and after a failed save, nothing is consented,
 * as the API and the voice service see it too.
 */
export interface ConsentChoices {
  camera: boolean;
  voice: boolean;
  dataUse: boolean;
}

/** The version of the consent text on screen (messages `safety.consent.*`). */
export const CONSENT_VERSION = "2026-09-27.1";

let current: ConsentChoices | null = null;
let saved = false;

/** New choices (or none): not saved yet. */
export function setConsentChoices(choices: ConsentChoices | null): void {
  current = choices;
  saved = false;
}

/** The API has recorded the current choices (POST /v1/sessions/{id}/consent returned them). */
export function markConsentSaved(): void {
  saved = current !== null;
}

export function consentChoices(): ConsentChoices | null {
  return current;
}

export function hasConsent(kind: "camera" | "voice"): boolean {
  return current !== null && saved && current.dataUse && current[kind];
}
