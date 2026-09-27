"use client";

import type { Schema } from "@careflow/contracts";
import { Card } from "@careflow/ui";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { api } from "@/lib/api";
import { useApiData } from "@/lib/useApiData";

import { ApiStatus } from "./ApiStatus";
import { SourcePopover } from "./SourcePopover";

type Briefing = Schema<"Briefing">;
type BriefingSection = Schema<"BriefingSection">;

/**
 * GET /v1/doctor/sessions/{session_id}/briefing (M1-16). Every line shows its provenance chip,
 * which opens its source (safety rule 4); a line whose source failed the check says "source
 * missing" in red instead of being hidden. The model's suggestion is always beside the nurse's
 * level, and the optional summary's citations highlight the lines they rest on.
 */
export function BriefingView({ sessionId }: { sessionId: string }) {
  const state = useApiData(
    () => api.GET("/v1/doctor/sessions/{session_id}/briefing", { params: { path: { session_id: sessionId } } }),
    `briefing:${sessionId}`,
  );
  return <ApiStatus state={state}>{(briefing) => <BriefingBody briefing={briefing} />}</ApiStatus>;
}

function BriefingBody({ briefing }: { briefing: Briefing }) {
  const [cited, setCited] = useState<ReadonlySet<string>>(new Set());
  return (
    <div className="flex flex-col gap-4" data-testid="briefing">
      <TriageBlock briefing={briefing} />
      <SummaryBlock briefing={briefing} onCite={(ids) => setCited(new Set(ids))} />
      {briefing.sections.map((section) => (
        <SectionCard key={section.id} section={section} briefing={briefing} cited={cited} />
      ))}
    </div>
  );
}

function TriageBlock({ briefing }: { briefing: Briefing }) {
  const t = useTranslations("briefing");
  const { confirmed, suggested, override_reason: reason } = briefing.triage;
  return (
    <Card title={t("triage")}>
      <p className="text-title font-bold">
        {t("confirmed", { scale: confirmed.scale, level: confirmed.level })}
        <span className="ms-3 text-body font-normal">
          {confirmed.action === "OVERRIDE" ? t("setByNurse") : t("confirmedByNurse")}
        </span>
      </p>
      {suggested ? (
        <p className="text-body">
          {t("suggested", {
            label: suggested.display_label ?? `${suggested.scale} ${suggested.level}`,
            confidence: Math.round(suggested.confidence * 100),
          })}
        </p>
      ) : (
        <p className="text-body">{t("noSuggestion")}</p>
      )}
      {reason ? (
        <p className="text-body">{t("reason", { code: reason.code.replaceAll("_", " "), note: reason.note ?? "" })}</p>
      ) : null}
    </Card>
  );
}

function SummaryBlock({ briefing, onCite }: { briefing: Briefing; onCite: (ids: string[]) => void }) {
  const t = useTranslations("briefing");
  if (briefing.summary_status === "dropped") {
    return (
      <p role="status" className="rounded-card border-2 border-border bg-secondary p-4 font-semibold text-on-secondary">
        {t("summaryDropped")}
      </p>
    );
  }
  if (briefing.summary_status !== "ok" || !briefing.summary) return null;
  return (
    <Card title={t("summary")}>
      <p className="mb-2 text-label text-ink-muted">{t("summaryNote")}</p>
      <ol className="flex flex-col gap-2">
        {briefing.summary.sentences.map((sentence, index) => (
          <li key={index} className="flex flex-wrap items-baseline gap-2">
            <span>{sentence.text}</span>
            <button
              type="button"
              className="rounded-control border-2 border-primary px-2 text-label font-semibold text-primary"
              onClick={() => onCite(sentence.fact_ids)}
            >
              {t("cites", { count: sentence.fact_ids.length })}
            </button>
          </li>
        ))}
      </ol>
    </Card>
  );
}

function SectionCard({
  section,
  briefing,
  cited,
}: {
  section: BriefingSection;
  briefing: Briefing;
  cited: ReadonlySet<string>;
}) {
  return (
    <Card title={section.title}>
      <ul className="flex flex-col gap-3" data-section={section.id}>
        {section.items.map((item, index) => {
          const highlighted = item.fact_id !== undefined && cited.has(item.fact_id);
          return (
            <li
              key={`${item.fact_id ?? "item"}-${index}`}
              className={`flex flex-wrap items-baseline gap-3 rounded-control px-2 py-1 ${
                highlighted ? "bg-secondary outline outline-4 outline-primary" : ""
              }`}
              data-fact-id={item.fact_id}
              data-cited={highlighted ? "true" : undefined}
            >
              <SourcePopover item={item} triage={briefing.triage} />
              <span className={item.source_missing ? "font-semibold text-prov-missing" : undefined}>{item.text}</span>
            </li>
          );
        })}
        {(section.notes ?? []).map((note, index) => (
          <li key={`note-${index}`} className="italic text-ink-muted" data-note={note.code}>
            {note.text}
          </li>
        ))}
      </ul>
    </Card>
  );
}
