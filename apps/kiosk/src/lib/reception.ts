export interface ReceptionDraft {
  urgentRequest: boolean;
  receptionStep: number;
  identityKind: "mobile" | "patient_id";
  identityValue: string;
  identityConsent: boolean;
  identityChallenge: string;
  maskedName: string;
  birthYear: string;
  patientIdentityToken: string;
  matchedProfile: { name: string; age_years: number; sex: "male" | "female" | "other" | "unknown"; phone?: string } | null;
  matchedIdentityToken: string;
  matchedHistory: { status: string; allergies: string[]; conditions: string[]; medications: string[] } | null;
  history: { status: string; allergies: string[]; conditions: string[]; medications: string[] } | null;
  visitFor: "self" | "child" | "elder" | "dependent";
  caregiverName: string;
  caregiverPhone: string;
  caregiverRelationship: "parent" | "carer" | "family" | "other";
  caregiverIdentityToken: string;
  claimKey: string;
  name: string;
  age: string;
  sex: "" | "male" | "female" | "other" | "unknown";
  phone: string;
  departmentId: string;
  doctorId: string;
  purpose: "" | "consultation" | "follow_up" | "test_results" | "other";
  reason: string;
  emergencySymptoms: Array<"chest_pain" | "short_of_breath" | "face_droop" | "arm_weakness" | "speech_difficulty" | "drowsy_or_confused">;
}

/** Patient registration stays in memory only and is cleared with the kiosk session. */
export function emptyReceptionDraft(): ReceptionDraft {
  return { urgentRequest: false, receptionStep: 0, identityKind: "mobile", identityValue: "", identityConsent: false, identityChallenge: "", maskedName: "", birthYear: "", patientIdentityToken: "", matchedProfile: null, matchedIdentityToken: "", matchedHistory: null, history: null, visitFor: "self", caregiverName: "", caregiverPhone: "", caregiverRelationship: "family", caregiverIdentityToken: "", claimKey: Array.from(crypto.getRandomValues(new Uint8Array(32)), (value) => value.toString(16).padStart(2, "0")).join(""), name: "", age: "", sex: "", phone: "", departmentId: "", doctorId: "", purpose: "", reason: "", emergencySymptoms: [] };
}
