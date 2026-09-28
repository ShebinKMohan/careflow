"use client";

import type { Schema } from "@careflow/contracts";
import { useTranslations } from "next-intl";
import { useEffect, useMemo, useState } from "react";
import { patientApi } from "@/lib/api";
import { useKiosk } from "@/lib/session";
import { MESSAGES } from "@/i18n/messages";
import { KioskIcon } from "./KioskIcon";

type Fact = Schema<"PatientSummaryFact">;
/** A scoped receipt: own facts, real rule codes, and a provisional model suggestion. */
export function PatientReceipt({ compact = false, refreshKey }: { compact?: boolean; refreshKey?: string }) {
  const t = useTranslations("patientReceipt");
  const { patient } = useKiosk();
  const [data, setData] = useState<Schema<"PatientIntakeSummary"> | null>(null);
  const [failed, setFailed] = useState(false);
  const token = patient?.token;
  const session = patient?.sessionId;
  const client = useMemo(() => token ? patientApi(token) : null, [token]);
  useEffect(() => {
    if (!client || !session) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const result = await client.GET("/v1/sessions/{session_id}/intake-summary", { params: { path: { session_id: session } }, signal: abort.signal }).catch(() => null);
      if (abort.signal.aborted) return;
      setFailed(!result?.data);
      if (result?.data) setData(result.data);
      timer = setTimeout(() => void poll(), 4000);
    };
    void poll();
    return () => { abort.abort(); clearTimeout(timer); };
  }, [client, session, refreshKey]);
  if (!patient) return null;
  if (!data || data.session_id !== session) return !compact && failed ? <p role="status">{t("unavailable")}</p> : null;
  const suggestion = data.suggestion;
  const alerts = data.alerts;
  if (compact && alerts.length === 0 && !data.assistance_requested) return null;
  const labels = MESSAGES[patient.language].patientReceipt.keys as Record<string, string>;
  const sources = MESSAGES[patient.language].patientReceipt.source as Record<string, string>;
  function factLabel(key: string) {
    const last = key.split(".").at(-1) ?? key;
    return labels[last] ?? last.replaceAll("_", " ");
  }
  function showFact(fact: Fact) {
    return <div key={fact.fact_id} className="receipt-fact"><dt>{factLabel(fact.key)}</dt><dd>{fact.value}<small>{sources[fact.provenance] ?? fact.provenance}{fact.simulated ? ` · ${t("simulated")}` : ""}</small></dd></div>;
  }
  const symptomKeys = ["chief_complaint", "symptom_onset_at", "pain_score", "pain_location"];
  const vitalKeys = ["vitals.heart_rate", "vitals.spo2", "vitals.temperature_c"];
  const symptomFacts = data.facts.filter(f => symptomKeys.includes(f.key));
  const history = data.facts.filter(f => f.key.startsWith("ehr_context."));
  return <section className={`patient-receipt ${compact ? "is-compact" : ""}`} aria-label={t("title")}>
    {!compact && <header><div><span className="kiosk-eyebrow">{t("reference")}</span><strong dir="ltr">{data.visit_reference}</strong></div><p>{t("referenceHint")}</p></header>}
    {(alerts.length > 0 || data.assistance_requested) && <div className="receipt-alert" role="status"><KioskIcon name="cross" /><div><h3>{t(alerts.length ? "alertTitle" : "assistanceTitle")}</h3><p>{t("alertHint")}</p><div className="receipt-codes">{alerts.map(a => <span key={a.code}><small>{t("ruleCode")}</small><bdi dir="ltr">{a.code}</bdi></span>)}{compact && <span><small>{t("reference")}</small><bdi dir="ltr">{data.visit_reference}</bdi></span>}</div></div></div>}
    {!compact && <>
      <section className="receipt-urgency"><div><span className="kiosk-eyebrow">{data.nurse_confirmed ? t("confirmed") : t("suggestion")}</span><h3>{data.nurse_confirmed ? `${data.confirmed_scale} ${data.confirmed_level}` : suggestion ? `${suggestion.scale} ${suggestion.level}` : t("pending")}</h3><p>{suggestion || data.nurse_confirmed ? t("notDiagnosis") : t("noSuggestion")}</p></div>
        {suggestion && <div className="receipt-model"><span>{t("model")} <bdi dir="ltr">{suggestion.model_id}</bdi></span><span>{t("confidence")} <strong>{Math.round(suggestion.confidence * 100)}%</strong></span><small dir="ltr">{suggestion.model_version}</small></div>}
      </section>
      {suggestion && <ul className="receipt-reasons">{suggestion.reasons.map((reason, i) => <li key={i}>{reason.text}</li>)}</ul>}
      <div className="receipt-facts-grid"><section><h3>{t("symptoms")}</h3><dl>{symptomFacts.length ? symptomFacts.map(showFact) : <p>{t("missing")}</p>}</dl></section>
        <section><h3>{t("vitals")}</h3><dl>{vitalKeys.map(key => { const fact = data.facts.find(f => f.key === key); return fact ? showFact(fact) : <div className="receipt-fact" key={key}><dt>{factLabel(key)}</dt><dd>{t("missing")}</dd></div>; })}{data.facts.filter(f => f.key.startsWith("vitals.") && !vitalKeys.includes(f.key)).map(showFact)}</dl></section>
        <section><h3>{t("history")}</h3><dl>{history.length ? history.map(showFact) : <p>{t("missing")}</p>}</dl><p className="receipt-camera-note">{t("cameraStatus")}</p></section></div>
      {data.warnings.length > 0 && <p className="receipt-warning">{t("warnings")}</p>}
      {failed && <p role="status">{t("unavailable")}</p>}
    </>}
  </section>;
}
