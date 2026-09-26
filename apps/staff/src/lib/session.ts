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
}
