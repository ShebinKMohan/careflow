"use client";

import { BigButton } from "@careflow/ui";
import { type Locale, setLocaleCookie } from "@careflow/ui/i18n";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

/** The kiosk start screen: pick a language, then go to the keypad. */
export function LanguageChoice() {
  const t = useTranslations("home");
  const router = useRouter();
  const choose = (locale: Locale) => {
    setLocaleCookie(locale);
    router.push("/checkin");
    router.refresh();
  };
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <BigButton lang="en" onClick={() => choose("en")}>
        {t("english")}
      </BigButton>
      <BigButton lang="ar" onClick={() => choose("ar")}>
        {t("arabic")}
      </BigButton>
    </div>
  );
}
