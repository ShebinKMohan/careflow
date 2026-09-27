import { createCareflowClient } from "@careflow/contracts/client";

import { readToken } from "./session";

/** The Prism mock (`make mock`) by default; `http://localhost:8000` for the real API. */
export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:4010";

export const api = createCareflowClient(API_BASE_URL, readToken);

/**
 * A client for the phone's session: it sends the latest stored token (renewed while the patient
 * waits, M2-06), or this one (from the QR claim) until one is stored.
 */
export function createCareflowClientFor(token: string) {
  return createCareflowClient(API_BASE_URL, () => readToken() ?? token);
}
