"use client";

import { LanguageToggle } from "@careflow/ui";
import { usePathname, useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { KioskIcon } from "./KioskIcon";

/** Screens where the language may still change: the first screen, and the staff-only device setup.
 * From check-in on, the session keeps the language the patient picked (M2-01). */
const LANGUAGE_SCREENS = new Set(["/", "/setup"]);

export function SiteHeader() {
  const locale = useLocale();
  const router = useRouter();
  const pathname = usePathname();
  const experience = useTranslations("experience");
  return (
    <header className="kiosk-header">
      <a href="#main-content" className="kiosk-skip">{experience("skipContent")}</a>
      <div className="kiosk-brand"><span className="kiosk-brand-mark"><KioskIcon name="cross" /></span><div><p className="kiosk-brand-name">{process.env.NEXT_PUBLIC_HOSPITAL_NAME ?? (locale === "ar" ? "مستشفى نورث ستار العام" : "Northstar General Hospital")}</p><p className="kiosk-brand-label">{locale === "ar" ? "مستشفى توضيحي · خدمة CareFlow" : "Example hospital · Patient services by CareFlow"}</p></div></div>
      <div className="kiosk-header-tools"><span className="kiosk-header-note"><span className="hospital-status-dot" />{locale === "ar" ? "تسجيل الوصول" : "Patient check-in"}</span>{LANGUAGE_SCREENS.has(pathname) ? <LanguageToggle locale={locale} onChange={() => router.refresh()} /> : <span className="kiosk-locale">{locale === "ar" ? "العربية" : "English"}</span>}</div>
    </header>
  );
}
