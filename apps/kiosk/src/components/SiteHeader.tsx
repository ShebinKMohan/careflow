"use client";

import { LanguageToggle } from "@careflow/ui";
import { usePathname, useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";

/** Screens where the language may still change: the first screen, and the staff-only device setup.
 * From check-in on, the session keeps the language the patient picked (M2-01). */
const LANGUAGE_SCREENS = new Set(["/", "/setup"]);

export function SiteHeader() {
  const t = useTranslations("header");
  const locale = useLocale();
  const router = useRouter();
  const pathname = usePathname();
  return (
    <header className="flex flex-wrap items-center justify-between gap-4 border-b-2 border-border bg-surface px-gutter py-3">
      <p className="text-title font-bold">{t("app")}</p>
      {LANGUAGE_SCREENS.has(pathname) ? <LanguageToggle locale={locale} onChange={() => router.refresh()} /> : null}
    </header>
  );
}
