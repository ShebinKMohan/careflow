import type { Locale } from "./i18n";

/** Shown in the footer of every CareFlow screen and in the README. */
export const PROTOTYPE_FOOTER = "Research prototype. Synthetic data only. Not for clinical use.";
/** The Arabic footer (machine-checked only; not validated by native speakers). */
export const PROTOTYPE_FOOTER_AR = "نموذج بحثي. بيانات اصطناعية فقط. ليس للاستخدام السريري.";

export const PROTOTYPE_FOOTER_TEXT: Record<Locale, string> = {
  en: PROTOTYPE_FOOTER,
  ar: PROTOTYPE_FOOTER_AR,
};

/** Said openly on every screen (M2-01): there is no native-speaker review of the Arabic. */
export const ARABIC_DISCLAIMER = "Arabic supported; not validated by native speakers.";
export const ARABIC_DISCLAIMER_AR = "العربية مدعومة؛ لم يتحقق منها متحدثون أصليون.";

export const ARABIC_DISCLAIMER_TEXT: Record<Locale, string> = {
  en: ARABIC_DISCLAIMER,
  ar: ARABIC_DISCLAIMER_AR,
};

export function PrototypeFooter({ locale }: { locale: Locale }) {
  return (
    <footer className="bg-footer px-gutter py-4 text-center text-label text-on-footer">
      <p lang={locale} data-testid="prototype-footer">
        {PROTOTYPE_FOOTER_TEXT[locale]}
      </p>
      <p lang={locale} data-testid="arabic-disclaimer">
        {ARABIC_DISCLAIMER_TEXT[locale]}
      </p>
    </footer>
  );
}
