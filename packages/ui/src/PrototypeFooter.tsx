import type { Locale } from "./i18n";
import ar from "./messages/ar.json";
import en from "./messages/en.json";

// The strings live in messages/*.json so the Arabic back-translation check covers them (M2-01).

/** Shown in the footer of every CareFlow screen and in the README. */
export const PROTOTYPE_FOOTER = en.safety.footer.prototype;
/** The Arabic footer (machine-checked only; not validated by native speakers). */
export const PROTOTYPE_FOOTER_AR = ar.safety.footer.prototype;

export const PROTOTYPE_FOOTER_TEXT: Record<Locale, string> = {
  en: PROTOTYPE_FOOTER,
  ar: PROTOTYPE_FOOTER_AR,
};

/** Said openly on every screen (M2-01): there is no native-speaker review of the Arabic. */
export const ARABIC_DISCLAIMER = en.safety.footer.arabicDisclaimer;
export const ARABIC_DISCLAIMER_AR = ar.safety.footer.arabicDisclaimer;

export const ARABIC_DISCLAIMER_TEXT: Record<Locale, string> = {
  en: ARABIC_DISCLAIMER,
  ar: ARABIC_DISCLAIMER_AR,
};

export function PrototypeFooter({ locale }: { locale: Locale }) {
  return (
    <footer className="cf-footer border-t border-line bg-footer px-gutter py-4 text-center text-caption text-on-footer">
      <p lang={locale} data-testid="prototype-footer">
        {PROTOTYPE_FOOTER_TEXT[locale]}
      </p>
      <p lang={locale} data-testid="arabic-disclaimer">
        {ARABIC_DISCLAIMER_TEXT[locale]}
      </p>
    </footer>
  );
}
