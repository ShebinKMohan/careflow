"use client";

import type { Schema } from "@careflow/contracts";
import { Card } from "@careflow/ui";
import { useFormatter, useTranslations } from "next-intl";
import { useState } from "react";

import { api } from "@/lib/api";
import { useApiData } from "@/lib/useApiData";

import { ProvenanceChip } from "./ProvenanceChip";

type ReceptionContext = Schema<"ReceptionContext">;

/** Show patient preferences as preferences; no specialist appointment or room is promised. */
export function ReceptionPreference({ context }: { context: ReceptionContext }) {
  const t = useTranslations("reception");
  if (!context.department_id || (context.intake_mode === "adaptive" && context.department_id === "unsure")) return <p className="staff-reception-notice">{t("routeAfterAssessment")}</p>;
  return (
    <div className="staff-reception-preference" data-testid="reception-preference">
      <span className="staff-reception-requested">{t("requested")}</span>
      <strong>{context.department_name}</strong>
      {context.doctor_name ? <span>{context.doctor_name}</span> : null}
      <span className="staff-reception-unconfirmed">{t("notBooked")}</span>
    </div>
  );
}

export function ReceptionSummary({ context }: { context: ReceptionContext }) {
  const t = useTranslations("reception");
  const nurse = useTranslations("nurse");
  const format = useFormatter();
  const patientFields = [
    { label: t("name"), value: context.patient.name },
    { label: t("age"), value: nurse("age", { age: context.patient.age_years }) },
    { label: t("sex"), value: nurse(`sex.${context.patient.sex}`) },
    ...(context.patient.phone ? [{ label: t("phone"), value: context.patient.phone }] : []),
  ];
  return (
    <Card title={t("title")} className="staff-reception-summary" data-testid="reception-summary">
      <p className="staff-reception-hospital">{t("hospital")} · {t("exampleHospital")}</p>
      {context.visit_for || context.identity_status ? <div className="staff-reception-identity">{context.visit_for ? <span>{t(`visitFor.${context.visit_for}`)}</span> : null}{context.identity_status ? <span>{t(`identity.${context.identity_status}`)}</span> : null}</div> : null}
      <div className="staff-reception-columns">
        <section aria-label={t("registration")}>
          <h3>{t("registration")}</h3>
          <dl className="staff-reception-fields">
            {patientFields.map((field) => <div key={field.label}><dt>{field.label}</dt><dd><span>{field.value}</span><ProvenanceChip provenance="PATIENT_STATED" /></dd></div>)}
          </dl>
        </section>
        <section aria-label={t("requestedCare")}>
          <h3>{t("requestedCare")}</h3>
          <ReceptionPreference context={context} />
          <p className="staff-reception-notice">{t("preferenceNotice")}</p>
          <p className="staff-reception-notice">{context.availability_notice}</p>
        </section>
      </div>
      {context.caregiver ? <section className="staff-reception-caregiver" aria-label={t("caregiver")}><h3>{t("caregiver")}</h3><p className="staff-reception-notice">{t("caregiverSeparation")}</p><dl className="staff-reception-fields"><div><dt>{t("name")}</dt><dd><span>{context.caregiver.name}</span><ProvenanceChip provenance="PATIENT_STATED" /></dd></div><div><dt>{t("relationship")}</dt><dd><span>{t(`relationships.${context.caregiver.relationship}`)}</span><ProvenanceChip provenance="PATIENT_STATED" /></dd></div>{context.caregiver.phone ? <div><dt>{t("phone")}</dt><dd><span>{context.caregiver.phone}</span><ProvenanceChip provenance="PATIENT_STATED" /></dd></div> : null}</dl></section> : null}
      <dl className="staff-reception-reason">
        <div><dt>{t("purpose")}</dt><dd>{context.intake_mode === "adaptive" ? <span>{t("adaptivePurpose")}</span> : <><span>{t(`purposes.${context.visit_purpose}`)}</span><ProvenanceChip provenance="PATIENT_STATED" /></>}</dd></div>
        <div><dt>{t("reason")}</dt><dd>{context.reason ? <><span>{context.reason}</span><ProvenanceChip provenance="PATIENT_STATED" /></> : <span className="staff-reception-notice">{t("reasonInConversation")}</span>}</dd></div>
      </dl>
      {context.emergency_symptoms.length ? <section className="staff-reception-symptoms" aria-label={t("emergencySymptoms")}><h3>{t("emergencySymptoms")}</h3><ul>{context.emergency_symptoms.map((symptom) => <li key={symptom}><span>{t(`symptoms.${symptom}`)}</span><ProvenanceChip provenance="PATIENT_STATED" /></li>)}</ul><p>{t("safetyNotice")}</p></section> : null}
      <p className="staff-reception-time">{t("registered", { time: format.dateTime(new Date(context.registered_at), { dateStyle: "medium", timeStyle: "short", numberingSystem: "latn" }) })}</p>
    </Card>
  );
}

/** Reception is optional on legacy visits; failed reads are visible instead of implying none. */
export function SessionReception({ sessionId }: { sessionId: string }) {
  const t = useTranslations("reception");
  const [round, setRound] = useState(0);
  const state = useApiData<ReceptionContext | null>(async () => {
    const { data, response } = await api.GET("/v1/sessions/{session_id}/reception", { params: { path: { session_id: sessionId } } });
    if (response.status === 404) return { data: null, response };
    return { data, response };
  }, `reception:${sessionId}:${round}`);
  if (state.status === "ready") return state.data?.patient ? <ReceptionSummary context={state.data} /> : null;
  if (state.status === "loading") return <p className="staff-reception-loading" role="status">{t("loading")}</p>;
  return <div className="staff-reception-load-error" role="status"><p>{t("unavailable")}</p><button type="button" className="staff-text-button" onClick={() => setRound((value) => value + 1)}>{t("retry")}</button></div>;
}
