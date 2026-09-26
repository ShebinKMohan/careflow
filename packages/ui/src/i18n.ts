/** Locale helpers shared by every app. No locale routing: the locale lives in a cookie. */

export const LOCALES = ["en", "ar"] as const;
export type Locale = (typeof LOCALES)[number];

export const DEFAULT_LOCALE: Locale = "en";
/** next-intl's conventional cookie name; read by each app's `src/i18n/request.ts`. */
export const LOCALE_COOKIE = "NEXT_LOCALE";

const ONE_YEAR_SECONDS = 60 * 60 * 24 * 365;

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}

/** Any unknown or missing value falls back to English. */
export function resolveLocale(value: string | null | undefined): Locale {
  return isLocale(value) ? value : DEFAULT_LOCALE;
}

export function direction(locale: Locale): "rtl" | "ltr" {
  return locale === "ar" ? "rtl" : "ltr";
}

/** Store the choice for the server to read on the next request (then call router.refresh()). */
export function setLocaleCookie(locale: Locale): void {
  document.cookie = `${LOCALE_COOKIE}=${locale}; path=/; max-age=${ONE_YEAR_SECONDS}; samesite=lax`;
}

/** Read the locale cookie in the browser (tests and client components). */
export function readLocaleCookie(cookieHeader: string = document.cookie): Locale {
  for (const part of cookieHeader.split(";")) {
    const [name, value] = part.trim().split("=");
    if (name === LOCALE_COOKIE) return resolveLocale(value);
  }
  return DEFAULT_LOCALE;
}

/** Clinic time zone for dates shown in the UI (the dev clinics use Asia/Dubai). */
export const DEFAULT_TIME_ZONE = "Asia/Dubai";
