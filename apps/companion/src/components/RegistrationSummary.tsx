"use client";

import type { Schema } from "@careflow/contracts";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { readSession } from "@/lib/session";

import { CompanionIcon } from "./CompanionIcon";

/** Server-recorded reception details. Legacy hand-over visits have no reception context. */
export function RegistrationSummary() {
  const t = useTranslations("reception");
  const [registration, setRegistration] = useState<Schema<"ReceptionContext"> | null>(null);
  useEffect(() => {
    const session = readSession();
    if (!session?.reception) return;
    let active = true;
    void api.GET("/v1/sessions/{session_id}/reception", {
      params: { path: { session_id: session.sessionId } },
      signal: AbortSignal.timeout(10_000),
    }).then(({ data }) => { if (active && data) setRegistration(data); }).catch(() => undefined);
    return () => { active = false; };
  }, []);
  if (!registration) return null;
  return <section className="companion-panel reception-submitted" aria-label={t("received")}>
    <div className="companion-section-title"><CompanionIcon name="check" /><h2>{t("received")}</h2></div>
    <p className="companion-muted">{t("receivedHint")}</p>
    <dl><div><dt>{t("department")}</dt><dd dir="auto">{registration.department_name}</dd></div><div><dt>{t("doctor")}</dt><dd dir="auto">{registration.doctor_name ?? t("anyDoctor")}</dd></div>{registration.intake_mode !== "adaptive" ? <div><dt>{t("purpose")}</dt><dd>{t(`purposes.${registration.visit_purpose}`)}</dd></div> : null}{registration.reason ? <div><dt>{t("reason")}</dt><dd dir="auto">{registration.reason}</dd></div> : null}</dl>
  </section>;
}
