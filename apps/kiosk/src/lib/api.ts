import { createCareflowClient } from "@careflow/contracts/client";

import { readDevice } from "./session";

/** The Prism mock (`make mock`) by default; `http://localhost:8000` for the real API. */
export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:4010";

/** Kiosk calls (check-in, vitals) use the kiosk_device token from the setup step. */
export const api = createCareflowClient(API_BASE_URL, () => readDevice()?.token);

/** Patient calls (turns, status) use the in-memory patient_session token. */
export function patientApi(token: string) {
  return createCareflowClient(API_BASE_URL, () => token);
}
