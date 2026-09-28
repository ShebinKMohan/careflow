"use client";

import type { Schema } from "@careflow/contracts";
import { Card } from "@careflow/ui";
import { useFormatter, useTranslations } from "next-intl";

import { factLabel } from "@/lib/factLabel";
import { indexFacts, type FactView } from "@/lib/facts";

import { ProvenanceChip } from "./ProvenanceChip";

type Facts = Schema<"StructuredFacts">;
type Needed = Schema<"TriageView">["needed_facts"];

/** The value and its actual recorded source travel together; a preview is never a clinical fact. */
export function ClinicalFact({ fact, label }: { fact: FactView; label?: string }) {
  const t = useTranslations("clinical");
  const format = useFormatter();
  const source = fact.source;
  const recorded = fact.recordedAt && Number.isFinite(Date.parse(fact.recordedAt))
    ? format.dateTime(new Date(fact.recordedAt), { dateStyle: "medium", timeStyle: "short", numberingSystem: "latn" })
    : null;
  return (
    <div className="staff-clinical-fact" data-fact-id={fact.factId}>
      <div className="staff-clinical-fact-value">{label ? <span className="staff-clinical-label">{label}</span> : null}<strong dir="auto">{fact.value || t("notRecorded")}</strong><ProvenanceChip provenance={fact.provenance} missing={!source} />{fact.criticality ? <span className="staff-clinical-criticality">{t("criticality", { value: fact.criticality })}</span> : null}</div>
      {source ? <details className="staff-clinical-source"><summary>{t("sourceDetails")}</summary><div>
        {source.kind === "transcript_span" ? <><q dir="auto">{source.quote}</q><p>{t("turn", { id: source.turn_id })}</p></> : null}
        {source.kind === "fhir" ? <p>{t("record", { reference: `${source.resource_type}/${source.id}`, version: source.version_id ?? "—" })}</p> : null}
        {source.kind === "device" ? <p>{t("device", { device: source.device_id, reading: source.reading_id })}</p> : null}
        {source.kind === "nurse" ? <p>{t("nurse", { id: source.staff_user_id })}</p> : null}
        {source.kind === "model" ? <p>{t("model", { id: source.model_id, version: source.model_version })}</p> : null}
        {recorded ? <p>{t("recorded", { time: recorded })}</p> : null}
      </div></details> : null}
    </div>
  );
}

/** Clinical context from /facts only. Missing history is never translated into a negative finding. */
export function ClinicalSnapshot({ facts, neededFacts = [], stale = false }: { facts: Facts | null; neededFacts: Needed; stale?: boolean }) {
  const t = useTranslations("clinical");
  if (!facts) return <section className="staff-clinical-unavailable" role="status"><strong>{t("unavailable")}</strong><p>{t("unavailableNote")}</p></section>;
  const indexed = [...indexFacts(facts).values()];
  const at = (path: string) => indexed.find((fact) => fact.path === path);
  const under = (prefix: string) => indexed.filter((fact) => fact.path.startsWith(`${prefix}.`));
  const renderField = (path: string, label: string) => {
    const fact = at(path);
    return <div key={path} className="staff-clinical-field" data-field={path}>{fact ? <ClinicalFact fact={fact} label={label} /> : <><span className="staff-clinical-label">{label}</span><span className="staff-clinical-missing">{t("notRecorded")}</span></>}</div>;
  };
  const onset = indexed.filter((fact) => fact.path === "symptom_onset_at" || fact.path === "chest_pain.onset" || /^symptoms\.\d+\.onset$/.test(fact.path));
  const symptoms = indexed.filter((fact) => /^symptoms\.\d+\.name$/.test(fact.path));
  const signs = indexed.filter((fact) => /^(fast|chest_pain|infection|child_danger|child_amber)\./.test(fact.path) && !fact.path.endsWith(".onset"));
  return (
    <section className="staff-clinical-snapshot" aria-label={t("title")} data-testid="clinical-snapshot">
      {stale ? <p className="staff-clinical-unavailable" role="status">{t("stale")}</p> : null}
      {facts.extraction_status === "incomplete" ? <p className="staff-clinical-incomplete">{t("incomplete")}</p> : null}
      <div className="staff-clinical-grid">
        <Card title={t("story")}>
          <div className="staff-clinical-story">
            {renderField("chief_complaint", t("complaint"))}
            {at("complaint_category") ? renderField("complaint_category", t("category")) : null}
            <div className="staff-clinical-field" data-field="onset"><span className="staff-clinical-label">{t("onset")}</span>{onset.length ? onset.map((fact) => <ClinicalFact key={fact.factId} fact={fact} />) : <span className="staff-clinical-missing">{t("notRecorded")}</span>}</div>
            <div className="staff-clinical-field"><span className="staff-clinical-label">{t("symptoms")}</span>{symptoms.length ? symptoms.map((fact) => <ClinicalFact key={fact.factId} fact={fact} />) : <span className="staff-clinical-missing">{t("notRecorded")}</span>}</div>
            {renderField("pain_score", t("pain"))}
            {at("pain_location") ? renderField("pain_location", t("painLocation")) : null}
            {at("arrival_mode") ? renderField("arrival_mode", t("arrival")) : null}
          </div>
        </Card>
        <Card title={t("observations")}>
          <p className="staff-clinical-note">{t("observationsNote")}</p>
          <div className="staff-clinical-vitals">
            {(["heart_rate", "spo2", "temperature_c", "respiratory_rate", "systolic_bp", "diastolic_bp", "acvpu", "on_supplemental_o2"] as const).map((key) => renderField(`vitals.${key}`, t(`vitals.${key}`)))}
          </div>
        </Card>
      </div>
      <Card title={t("history")}>
        <div className="staff-clinical-history">
          {(["conditions", "medications", "allergies"] as const).map((key) => <section key={key} aria-label={t(`historyLabels.${key}`)}><h3>{t(`historyLabels.${key}`)}</h3>{under(`ehr_context.${key}`).length ? under(`ehr_context.${key}`).map((fact) => <ClinicalFact key={fact.factId} fact={fact} />) : <p className="staff-clinical-missing">{t(`historyMissing.${key}`)}</p>}</section>)}
        </div>
        {at("allergies_confirm") ? <div className="staff-clinical-patient-allergies"><ClinicalFact fact={at("allergies_confirm")!} label={t("allergyAnswer")} /></div> : null}
      </Card>
      {signs.length ? <Card title={t("reportedSigns")}><div className="staff-clinical-signs">{signs.map((fact) => <ClinicalFact key={fact.factId} fact={fact} label={factLabel(fact.path)} />)}</div></Card> : null}
      {neededFacts.length ? <section className="staff-clinical-outstanding" aria-label={t("outstanding")}><h3>{t("outstanding")}</h3><ul>{neededFacts.map((needed) => <li key={needed.key}>{factLabel(needed.key)} <span>{needed.audience === "nurse" ? t("nurseToRecord") : t("patientToAnswer")}</span></li>)}</ul></section> : null}
    </section>
  );
}
