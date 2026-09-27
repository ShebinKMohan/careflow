import { createCareflowClient } from "@careflow/contracts/client";

import { readToken } from "./session";

/** The Prism mock (`make mock`) by default; `http://localhost:8000` for the real API. */
export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:4010";

export const api = createCareflowClient(API_BASE_URL, readToken);

/** A client that sends this token (the phone-channel session token from the QR claim). */
export function createCareflowClientFor(token: string) {
  return createCareflowClient(API_BASE_URL, () => token);
}
