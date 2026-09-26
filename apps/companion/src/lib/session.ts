/**
 * Browser-only storage for the patient_session token from the QR hand-over (tab-scoped,
 * cleared when the tab closes). The service worker never sees or caches it.
 */
const TOKEN_KEY = "careflow.companion.session-token";

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
