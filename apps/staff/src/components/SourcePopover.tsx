"use client";

import { useFormatter, useTranslations } from "next-intl";
import { type KeyboardEvent, useEffect, useId, useRef, useState } from "react";

import type { Schema } from "@careflow/contracts";

import { highlight } from "@/lib/highlight";

import { ProvenanceChip } from "./ProvenanceChip";

type BriefingItem = Schema<"BriefingItem">;
type BriefingTriage = Schema<"BriefingTriage">;

/** The FHIR server a doctor can open an EHR resource on (the demo HAPI by default). */
export const FHIR_BASE_URL = (process.env.NEXT_PUBLIC_FHIR_BASE_URL ?? "http://localhost:8080/fhir").replace(/\/+$/, "");

/**
 * An item's provenance chip as a button; it opens where the item came from (the patient's words,
 * the EHR resource, the device reading, the nurse, or the model beside the nurse's level). It
 * opens on click, Enter or Space, and closes on Escape, returning focus to the chip.
 */
export function SourcePopover({ item, triage }: { item: BriefingItem; triage: BriefingTriage }) {
  const t = useTranslations("source");
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const button = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    function close(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    }
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [open]);

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "Escape" && open) {
      event.preventDefault();
      setOpen(false);
    }
  }

  return (
    <span className="relative inline-flex">
      <button
        ref={button}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
        onKeyDown={onKeyDown}
        className="rounded-control focus-visible:outline-4 focus-visible:outline-offset-2 focus-visible:outline-focus"
      >
        <ProvenanceChip provenance={item.provenance} missing={item.source_missing ?? false} />
        <span className="sr-only">{t("show")}</span>
      </button>
      {open ? (
        <div
          id={panelId}
          role="dialog"
          aria-label={t("title")}
          className="absolute start-0 top-full z-20 mt-2 w-[min(28rem,80vw)] rounded-card border-2 border-border bg-surface p-4 text-body text-ink shadow-lg"
        >
          <SourceDetails item={item} triage={triage} />
        </div>
      ) : null}
    </span>
  );
}

function SourceDetails({ item, triage }: { item: BriefingItem; triage: BriefingTriage }) {
  const t = useTranslations("source");
  const format = useFormatter();
  const when = format.dateTime(new Date(item.recorded_at), { dateStyle: "medium", timeStyle: "short", numberingSystem: "latn" });
  const source = item.source;
  return (
    <div className="flex flex-col gap-2">
      {item.source_missing ? (
        <p role="note" className="font-semibold text-prov-missing">
          {t("missing")}
        </p>
      ) : null}
      {source.kind === "transcript_span" ? (
        <>
          <p>
            {t("patientWords")} <PatientQuote quote={source.quote} />
          </p>
          <p className="text-label text-ink-muted">
            {t("turn", { turn: source.turn_id, start: source.start, end: source.end, when })}
          </p>
        </>
      ) : null}
      {source.kind === "fhir" ? (
        <>
          <p>
            {t("ehr", { type: source.resource_type, id: source.id, version: source.version_id ?? "-" })}
          </p>
          <a
            className="font-semibold text-primary underline"
            href={`${FHIR_BASE_URL}/${encodeURIComponent(source.resource_type)}/${encodeURIComponent(source.id)}`}
            target="_blank"
            rel="noreferrer noopener"
          >
            {t("openEhr")}
          </a>
        </>
      ) : null}
      {source.kind === "device" ? (
        <p>{t("device", { device: source.device_id, reading: source.reading_id, when })}</p>
      ) : null}
      {source.kind === "nurse" ? <p>{t("nurse", { nurse: source.staff_user_id, when })}</p> : null}
      {source.kind === "model" ? (
        <>
          <p>{t("model", { model: source.model_id, version: source.model_version })}</p>
          {triage.suggested && triage.suggested.model_version === source.model_version ? (
            <p>{t("confidence", { value: Math.round(triage.suggested.confidence * 100) })}</p>
          ) : null}
          <p className="font-semibold">
            {t("suggestionOnly", { scale: triage.confirmed.scale, level: triage.confirmed.level })}
          </p>
        </>
      ) : null}
    </div>
  );
}

/** The patient's words, marked; the briefing keeps only the cited span (transcripts stay in the
 * session), so the whole quote is the span. */
function PatientQuote({ quote }: { quote: string }) {
  const { before, marked, after } = highlight(quote, 0, Array.from(quote).length);
  return (
    <q>
      {before}
      <mark className="rounded bg-prov-patient-tint px-1 text-prov-patient">{marked}</mark>
      {after}
    </q>
  );
}
