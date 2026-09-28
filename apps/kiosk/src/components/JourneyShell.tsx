"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import { useLocale } from "next-intl";
import type { ReactNode } from "react";
import { KioskIcon } from "./KioskIcon";

/** Registration owns editable in-memory steps; this shell never advances a patient session. */
export function JourneyShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const ar = useLocale() === "ar";
  if (pathname === "/") return <main id="main-content" className="kiosk-home">{children}</main>;
  if (pathname === "/setup") return <main id="main-content" className="kiosk-setup">{children}</main>;
  const labels = ar ? ["بياناتك", "المحادثة", "القياسات", "مراجعة التمريض"] : ["Your details", "Conversation", "Health check", "Nurse review"];
  const stage = pathname === "/consent" ? 0 : pathname === "/vitals" ? 2 : pathname === "/done" ? 3 : 1;
  return <main id="main-content" className="kiosk-journey reception-shell">
    <div className="reception-shell-top">
      <span className="reception-shell-label"><KioskIcon name="record" />{ar ? "تسجيل الوصول إلى المستشفى" : "Hospital check-in"}</span>
      {pathname !== "/checkin" && <ol className="reception-shell-steps" aria-label={ar ? "تقدم التسجيل" : "Check-in progress"}>
        {labels.map((label, i) => <li key={label} aria-current={stage === i ? "step" : undefined} className={i === stage ? "is-current" : i < stage ? "is-complete" : ""}><span>{i < stage ? <KioskIcon name="check" /> : i + 1}</span>{label}</li>)}
      </ol>}
    </div>
    <section className="journey-content reception-content">
      {pathname === "/consent" && <Link href="/checkin" className="reception-back"><span aria-hidden="true">←</span>{ar ? "رجوع" : "Back"}</Link>}
      <div className="journey-page-body">{children}</div>
    </section>
    <p className="reception-support"><KioskIcon name="help" />{ar ? "تحتاج مساعدة أو تشعر بتدهور؟ تحدث إلى موظف المستشفى الآن." : "Need a hand or feeling seriously unwell? Please alert a member of hospital staff now."}</p>
  </main>;
}
