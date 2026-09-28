"use client";

import { type Locale, setLocaleCookie } from "@careflow/ui/i18n";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { KioskIcon } from "./KioskIcon";

/** The kiosk start screen: pick a language, then the consent screen (M2-05), then check-in. */
export function LanguageChoice() {
  const t = useTranslations("home");
  const router = useRouter();
  const choose = (locale: Locale) => {
    setLocaleCookie(locale);
    router.push("/consent");
    router.refresh();
  };
  return (
    <div className="language-choices">
      <button type="button" className="language-card" lang="en" aria-label={t("english")} onClick={() => choose("en")}>
        <span className="language-symbol" aria-hidden="true">Aa</span><span>{t("english")}</span><KioskIcon name="arrow" />
      </button>
      <button type="button" className="language-card" lang="ar" aria-label={t("arabic")} onClick={() => choose("ar")}>
        <span className="language-symbol" aria-hidden="true">ع</span><span>{t("arabic")}</span><KioskIcon name="arrow" />
      </button>
    </div>
  );
}
