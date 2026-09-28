"use client";

import { LanguageToggle } from "@careflow/ui";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";

import { CompanionIcon } from "./CompanionIcon";

export function SiteHeader() {
  const t = useTranslations("header");
  const locale = useLocale();
  const router = useRouter();
  return (
    <header className="companion-header">
      <div className="companion-header-inner">
        <Link href="/" className="companion-brand" aria-label={t("app")}>
          <span className="companion-brand-mark"><CompanionIcon name="plus" /></span>
          <span><strong>{process.env.NEXT_PUBLIC_HOSPITAL_NAME ?? "Northstar General Hospital"}</strong><small>{t("companion")}</small></span>
        </Link>
        <LanguageToggle locale={locale} onChange={() => router.refresh()} />
      </div>
    </header>
  );
}
