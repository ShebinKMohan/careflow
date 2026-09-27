import { createCareflowClient } from "@careflow/contracts/client";

import { readDevice } from "./session";

/** The Prism mock (`make mock`) by default; `http://localhost:8000` for the real API. */
export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:4010";

/** Kiosk calls (check-in, vitals) use the kiosk_device token from the setup step. */
export const api = createCareflowClient(API_BASE_URL, () => readDevice()?.token);

/**
 * Fired (on `window`, with the token as `detail`) when the API refuses a patient's token: the
 * session continued on the patient's phone (M2-06) or the token ran out. `RevokedGuard` then
 * forgets that patient: nothing about them may stay on the shared kiosk.
 */
export const PATIENT_REVOKED = "careflow:patient-revoked";

/** Patient calls (turns, status) use the in-memory patient_session token. */
export function patientApi(token: string) {
  const client = createCareflowClient(API_BASE_URL, () => token);
  client.use({
    onResponse({ response }) {
      if (response.status === 401) window.dispatchEvent(new CustomEvent(PATIENT_REVOKED, { detail: token }));
      return response;
    },
  });
  return client;
}
