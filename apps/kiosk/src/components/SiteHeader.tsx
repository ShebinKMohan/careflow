"use client";

import { LanguageToggle } from "@careflow/ui";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";

export function SiteHeader() {
  const t = useTranslations("header");
  const locale = useLocale();
  const router = useRouter();
  return (
    <header className="flex flex-wrap items-center justify-between gap-4 border-b-2 border-border bg-surface px-gutter py-3">
      <p className="text-title font-bold">{t("app")}</p>
      <LanguageToggle locale={locale} onChange={() => router.refresh()} />
    </header>
  );
}
