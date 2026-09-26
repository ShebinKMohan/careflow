"use client";

import { type Locale, LOCALES, setLocaleCookie } from "./i18n";

const LABELS: Record<Locale, string> = { en: "English", ar: "العربية" };

export interface LanguageToggleProps {
  locale: Locale;
  /** Called after the cookie is set; apps pass `() => router.refresh()`. */
  onChange?: (locale: Locale) => void;
  className?: string;
}

/** Switches between English and Arabic by setting the NEXT_LOCALE cookie. */
export function LanguageToggle({ locale, onChange, className = "" }: LanguageToggleProps) {
  return (
    <div role="group" aria-label="Language / اللغة" className={`flex gap-2 ${className}`}>
      {LOCALES.map((option) => (
        <button
          key={option}
          type="button"
          lang={option}
          aria-pressed={option === locale}
          onClick={() => {
            setLocaleCookie(option);
            onChange?.(option);
          }}
          className={`min-h-touch min-w-touch rounded-control border-2 border-border px-4 text-label font-semibold ${
            option === locale ? "bg-primary text-on-primary" : "bg-surface text-ink"
          }`}
        >
          {LABELS[option]}
        </button>
      ))}
    </div>
  );
}
