import { createCareflowClient } from "@careflow/contracts/client";

import { API_BASE_URL } from "./api";
import { clearSession, readSession, readToken, saveToken } from "./session";

/** Renew this long before the token runs out (30-minute tokens: about every 20 minutes). */
export const RENEW_BEFORE_MS = 10 * 60_000;
/** After a failed renewal (no network, API down), try again this soon. */
export const RETRY_MS = 30_000;

/**
 * When the stored JWT runs out, in ms since the epoch, read from its payload; null when unreadable.
 * The phone only schedules with it: the API verifies every token.
 */
export function tokenExpiry(token: string): number | null {
  const payload = token.split(".")[1];
  if (!payload) return null;
  try {
    const base64 = payload.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(payload.length / 4) * 4, "=");
    const claims = JSON.parse(atob(base64)) as { exp?: unknown };
    return typeof claims.exp === "number" ? claims.exp * 1000 : null;
  } catch {
    return null;
  }
}

export type Renewal = "renewed" | "ended" | "unavailable";

/**
 * Swap the stored phone token for a new one (M2-06, `POST /v1/sessions/{id}/renew`). "ended": the
 * token or the visit is over (expired, revoked, closed, or checked in more than 12 hours ago), so
 * the tab's credentials are cleared and the pages send the patient to the desk. "unavailable": try
 * again later.
 */
export async function renewToken(): Promise<Renewal> {
  const session = readSession();
  const token = readToken();
  if (!session || !token) return "ended";
  try {
    const { data, response } = await createCareflowClient(API_BASE_URL, () => token).POST(
      "/v1/sessions/{session_id}/renew",
      { params: { path: { session_id: session.sessionId } } },
    );
    if (data) {
      saveToken(data.session_token);
      return "renewed";
    }
    if ([401, 403, 404, 409].includes(response.status)) {
      clearSession();
      return "ended";
    }
    return "unavailable";
  } catch {
    return "unavailable";
  }
}
