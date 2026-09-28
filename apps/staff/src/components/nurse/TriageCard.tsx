"use client";

import type { Schema } from "@careflow/contracts";
import { Card } from "@careflow/ui";
import { useTranslations } from "next-intl";
import { type KeyboardEvent, useState } from "react";

import { api } from "@/lib/api";
import {
  decisionBody,
  NO_SUGGESTION_NOTE,
  OVERRIDE_REASONS,
  type OverrideReason,
  reasonGiven,
} from "@/lib/decision";
import { factLabel } from "@/lib/factLabel";
import type { FactView } from "@/lib/facts";

import { ClinicalFact } from "./ClinicalSnapshot";
import { levelLabel } from "./LevelChip";
import { ProvenanceChip } from "./ProvenanceChip";

type TriageView = Schema<"TriageView">;
type DecisionResult = Schema<"DecisionResult">;
type SessionWarning = Schema<"SessionWarning">;
type Level = number;

const LEVELS: readonly Level[] = [1, 2, 3, 4, 5];
const PRIMARY = "min-h-touch rounded-control bg-primary px-6 font-semibold text-on-primary disabled:opacity-60";
const SECONDARY = "min-h-touch rounded-control border-2 border-border bg-surface px-6 font-semibold";

/** Enter never decides a level: only a click or tap (or Space) does, so no accidental confirm. */
function ignoreEnter(event: KeyboardEvent<HTMLButtonElement>) {
  if (event.key === "Enter") event.preventDefault();
}

function newDecisionId(): string {
  return `dec_${crypto.randomUUID().replaceAll("-", "")}`;
}

/**
 * The triage card (safety rule 3): the model's suggestion, clearly a suggestion, and the nurse's
 * decision. "Confirm level N" is never auto-focused and ignores Enter. "Change level" needs a
 * level and a reason. With no suggestion the nurse must choose a level (pre-filled reason). The
 * token and room appear only after the API accepted the decision.
 */
export function TriageCard({
  triage,
  facts,
  warnings,
  nurseUserId,
  onDecided,
}: {
  triage: TriageView;
  facts: Map<string, FactView>;
  warnings: SessionWarning[];
  nurseUserId: string | null;
  onDecided: (result: DecisionResult) => void;
}) {
  const t = useTranslations("triage");
  const reception = useTranslations("reception");
  const tSafety = useTranslations("safety.triage");
  const suggestion = triage.suggestion;
  // `changing` is a nurse's explicit edit, not the temporary absence of a model result.
  const [changing, setChanging] = useState(false);
  const [level, setLevel] = useState<Level | null>(null);
  const [reason, setReason] = useState<OverrideReason>({ code: null, note: "" });
  const displayedReason: OverrideReason = !suggestion && reason.code === null && reason.note === ""
    ? { code: "other", note: NO_SUGGESTION_NOTE }
    : reason;
  const showOverride = !suggestion || changing;
  const [status, setStatus] = useState<"idle" | "saving" | "failed">("idle");
  const [result, setResult] = useState<DecisionResult | null>(null);
  const scale = suggestion?.scale ?? "KTAS";

  async function decide(chosen: Level, override: OverrideReason | null) {
    if (!nurseUserId || status === "saving") return;
    setStatus("saving");
    const body = decisionBody({
      sessionId: triage.session_id,
      suggestion,
      level: chosen,
      override,
      nurseUserId,
      alertIds: triage.alerts.map((alert) => alert.alert_id),
      scale,
      decisionId: newDecisionId(),
      decidedAt: new Date().toISOString(),
    });
    try {
      const { data } = await api.POST("/v1/nurse/sessions/{session_id}/decision", {
        params: { path: { session_id: triage.session_id } },
        body,
      });
      if (!data) {
        setStatus("failed");
        return;
      }
      setStatus("idle");
      setResult(data);
      onDecided(data);
    } catch {
      setStatus("failed");
    }
  }

  const decided = result?.decision ?? triage.decision;
  return (
    <Card title={t("title")}>
      {warnings.length ? (
        <ul className="mb-4 flex flex-col gap-2" data-testid="triage-warnings">
          {warnings.map((warning) => (
            <li key={warning} className="rounded-control border-2 border-danger px-3 py-2 font-semibold text-danger">
              {tSafety(`warnings.${warning}`)}
            </li>
          ))}
        </ul>
      ) : null}

      {suggestion ? (
        <section className="staff-triage-suggestion mb-4 flex flex-col gap-2 rounded-card border border-border p-gutter" aria-label={t("suggestion")} data-level={suggestion.level}>
          <p className="text-label font-semibold uppercase">{tSafety("notFinal")}</p>
          <p className="text-title font-bold">
            {t("suggested", { label: suggestion.display_label ?? levelLabel(suggestion.scale, suggestion.level) })}
          </p>
          <p className="staff-scale-level">{t("scaleLevel", { scale: suggestion.scale, level: suggestion.level })}</p>
          <p>
            {t("confidence", { percent: Math.round(suggestion.confidence * 100) })}
            <span className="staff-model-identity">{t("modelIdentity", { id: suggestion.model_id, version: suggestion.model_version })}</span>
          </p>
          <h3 className="font-semibold">{t("why")}</h3>
          <ol className="flex list-decimal flex-col gap-2 ps-6">
            {suggestion.reasons.slice(0, 3).map((item) => (
              <li key={item.text} className="staff-suggestion-reason" data-testid="suggestion-reason">
                <div className="staff-suggestion-reason-title"><span>{item.text}</span><ProvenanceChip provenance="MODEL_INFERRED" /></div>
                {item.fact_ids.length ? <ul className="staff-suggestion-evidence">{item.fact_ids.map((id) => {
                  const fact = facts.get(id);
                  return <li key={id}>{fact ? <ClinicalFact fact={fact} label={factLabel(fact.path)} /> : <><ProvenanceChip provenance="MODEL_INFERRED" missing /><span className="staff-clinical-note">{t("evidenceUnavailable")}</span></>}</li>;
                })}</ul> : <p className="staff-clinical-note">{t("noEvidence")}</p>}
              </li>
            ))}
          </ol>
        </section>
      ) : (
        <p className="mb-4 rounded-control border-2 border-danger px-3 py-2 font-semibold text-danger">{t("none")}</p>
      )}

      {decided ? (
        <section className="staff-confirmed flex flex-col gap-2" aria-label={t("confirmedTitle")} data-testid="decision-result">
          <p className="text-title font-bold">{t("confirmedLevel", { label: levelLabel(decided.scale, decided.final_level) })}</p>
          {result?.token ? (
            <><p className="staff-reception-notice">{reception("assignedQueue")}: <strong>{result.token.department}</strong></p><p className="text-title">
              {t.rich("token", { code: result.token.token_code, ltr: (chunks) => <bdi dir="ltr">{chunks}</bdi> })}
              {result.token.room ? <> · {t.rich("room", { room: result.token.room, ltr: (chunks) => <bdi dir="ltr">{chunks}</bdi> })}</> : null}
            </p></>
          ) : result ? (
            <p>{t("noToken")}</p>
          ) : null}
        </section>
      ) : (
        <div className="flex flex-col gap-4">
          {suggestion && !changing ? (
            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                className={PRIMARY}
                disabled={status === "saving" || !nurseUserId}
                onKeyDown={ignoreEnter}
                onClick={() => void decide(suggestion.level, null)}
              >
                {t("confirm", { level: suggestion.level })}
              </button>
              <button type="button" className={SECONDARY} onKeyDown={ignoreEnter} onClick={() => setChanging(true)}>
                {t("change")}
              </button>
            </div>
          ) : null}
          {showOverride ? (
            <section className="flex flex-col gap-4" aria-label={t("change")}>
              <fieldset className="flex flex-col gap-2">
                <legend className="font-semibold">{t("pickLevel")}</legend>
                <div className="flex flex-wrap gap-2">
                  {LEVELS.map((option) => (
                    <button
                      key={option}
                      type="button"
                      aria-pressed={level === option}
                      className={`min-h-touch min-w-touch rounded-control border-2 border-border px-4 font-semibold ${
                        level === option ? "bg-primary text-on-primary" : "bg-surface"
                      }`}
                      onKeyDown={ignoreEnter}
                      onClick={() => { setChanging(true); setLevel(option); }}
                    >
                      {levelLabel(scale, option)}
                    </button>
                  ))}
                </div>
              </fieldset>
              <fieldset className="flex flex-col gap-2">
                <legend className="font-semibold">{t("reason")}</legend>
                <div className="flex flex-wrap gap-2">
                  {OVERRIDE_REASONS.map((code) => (
                    <button
                      key={code}
                      type="button"
                      aria-pressed={displayedReason.code === code}
                      className={`min-h-touch rounded-control border-2 border-border px-4 ${
                        displayedReason.code === code ? "bg-primary text-on-primary" : "bg-surface"
                      }`}
                      onKeyDown={ignoreEnter}
                      onClick={() => { setChanging(true); setReason((current) => ({ ...current, code })); }}
                    >
                      {t(`reasonCodes.${code}`)}
                    </button>
                  ))}
                </div>
                <label className="flex flex-col gap-2">
                  {t("note")}
                  <textarea
                    value={displayedReason.note}
                    maxLength={500}
                    rows={2}
                    onChange={(event) => { setChanging(true); setReason({ ...displayedReason, note: event.target.value }); }}
                    className="rounded-control border-2 border-border bg-surface p-3 text-body"
                  />
                </label>
              </fieldset>
              <div className="flex flex-wrap gap-3">
                <button
                  type="button"
                  className={PRIMARY}
                  disabled={level === null || !reasonGiven(displayedReason) || status === "saving" || !nurseUserId}
                  onKeyDown={ignoreEnter}
                  onClick={() => level !== null && void decide(level, displayedReason)}
                >
                  {level === null ? t("submitOverrideNoLevel") : t("submitOverride", { level })}
                </button>
                {suggestion ? (
                  <button type="button" className={SECONDARY} onKeyDown={ignoreEnter} onClick={() => setChanging(false)}>
                    {t("cancel")}
                  </button>
                ) : null}
              </div>
            </section>
          ) : null}
          {status === "failed" ? (
            <p role="alert" className="font-semibold text-danger">
              {t("failed")}
            </p>
          ) : null}
        </div>
      )}

      <FactList facts={facts} />
    </Card>
  );
}

/** Every fact, grouped by where it sits in StructuredFacts, each with its provenance (rule 4). */
function FactList({ facts }: { facts: Map<string, FactView> }) {
  const t = useTranslations("triage");
  const groups = new Map<string, FactView[]>();
  for (const fact of facts.values()) {
    const group = fact.path.split(".")[0] ?? "";
    groups.set(group, [...(groups.get(group) ?? []), fact]);
  }
  return (
    <details className="staff-fact-list mt-6" aria-label={t("facts")}>
      <summary className="text-title font-semibold">{t("facts")}</summary>
      {groups.size === 0 ? <p>{t("noFacts")}</p> : null}
      {[...groups.entries()].map(([group, items]) => (
        <div key={group}>
          <h4 className="font-semibold capitalize">{group.replaceAll("_", " ")}</h4>
          <ul className="flex flex-col gap-1">
            {items.map((fact) => (
              <li key={fact.factId} className="flex flex-wrap items-center gap-2">
                <span>
                  {factLabel(fact.path)}: <strong>{fact.value}</strong>
                </span>
                <ProvenanceChip provenance={fact.provenance} />
              </li>
            ))}
          </ul>
        </div>
      ))}
    </details>
  );
}
