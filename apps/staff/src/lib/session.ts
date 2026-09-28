/**
 * Browser-only token storage for staff screens (tab-scoped, cleared when the tab closes).
 * The sign-in form is a development stub; real staff login UX is out of scope for M0-08.
 */
const TOKEN_KEY = "careflow.staff.token";

export function readToken(): string | null {
  try {
    return window.sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function saveToken(token: string): void {
  window.sessionStorage.setItem(TOKEN_KEY, token);
  window.dispatchEvent(new Event("careflow:staff-session"));
}

/** Notify mounted navigation in this tab; storage events also handle other documents. */
export function subscribeToken(listener: () => void): () => void {
  window.addEventListener("careflow:staff-session", listener);
  window.addEventListener("storage", listener);
  return () => {
    window.removeEventListener("careflow:staff-session", listener);
    window.removeEventListener("storage", listener);
  };
}

export function clearToken(): void {
  window.sessionStorage.removeItem(TOKEN_KEY);
  window.dispatchEvent(new Event("careflow:staff-session"));
}
