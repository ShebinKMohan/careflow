import type { Schema } from "@careflow/contracts";

import { api, createCareflowClientFor } from "./api";
import { clearSession, type PhoneSession, readSession, readToken, saveSession, saveToken } from "./session";

export type Question = Schema<"IntakeQuestion">;
type VisitState = Schema<"SessionState">;
export type CurrentIntake =
  | { status: "ready"; question: Question | null; revision: number | null; state: VisitState }
  | { status: "expired" }
  | { status: "unavailable" };
export type PhoneVisit =
  | { status: "ready"; session: PhoneSession; token: string; question: Question | null; revision: number | null; state: VisitState }
  | { status: "loading" | "empty" | "invalid" | "failed" | "expired" | "unavailable" };

export function isIntakeRevision(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

/** Read the server checkpoint. Missing questions never mean that an intake is finished. */
export async function currentIntake(sessionId: string, token: string): Promise<CurrentIntake> {
  try {
    const { data, response } = await createCareflowClientFor(token).GET("/v1/sessions/{session_id}/status", {
      params: { path: { session_id: sessionId } },
    });
    if (response.status === 401 || response.status === 403 || response.status === 404) {
      clearSession();
      return { status: "expired" };
    }
    if (data?.state === "closed" || data?.state === "no_show") {
      clearSession();
      return { status: "expired" };
    }
    if (!data) return { status: "unavailable" };
    if (data.state !== "intake") return { status: "ready", question: null, revision: null, state: data.state };
    if (!data.current_question || !isIntakeRevision(data.intake_revision)) {
      return { status: "unavailable" };
    }
    return { status: "ready", question: data.current_question, revision: data.intake_revision, state: data.state };
  } catch {
    return { status: "unavailable" };
  }
}

export async function resumeVisit(): Promise<PhoneVisit> {
  const session = readSession();
  const token = readToken();
  if (!session || !token) return { status: "empty" };
  const current = await currentIntake(session.sessionId, token);
  return current.status === "ready" ? { ...current, session, token } : current;
}

/** The code is never saved. Only the tab-scoped credential and minimal visit identity survive reload. */
export async function claimVisit(code: string, onClaimed: () => void = () => undefined): Promise<PhoneVisit> {
  try {
    const { data, response } = await api.POST("/v1/checkin/qr/claim", { body: { qr_token: code } });
    if (!data) return { status: response.status >= 500 ? "unavailable" : "failed" };
    const session = { sessionId: data.session_id, language: data.language, display: data.patient_display };
    saveToken(data.session_token);
    saveSession(session);
    onClaimed();
    return resumeVisit();
  } catch {
    return { status: "unavailable" };
  }
}
