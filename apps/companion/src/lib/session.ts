/**
 * Browser-only storage for the phone's session after the QR hand-over (M2-06): the
 * patient_session token and which session it is for. Tab-scoped sessionStorage, cleared when the
 * tab closes; never localStorage, never a cookie. The service worker never sees or caches it.
 */
const TOKEN_KEY = "careflow.companion.session-token";
const SESSION_KEY = "careflow.companion.session";

export interface PhoneSession {
  sessionId: string;
  language: "en" | "ar";
  /** Initials only, e.g. "M. S.". */
  display: string;
}

export function readToken(): string | null {
  try {
    return window.sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function saveToken(token: string): void {
  window.sessionStorage.setItem(TOKEN_KEY, token);
}

export function readSession(): PhoneSession | null {
  try {
    const raw = window.sessionStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as PhoneSession) : null;
  } catch {
    return null;
  }
}

export function saveSession(session: PhoneSession): void {
  window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
}
