"use client";

import type { Schema } from "@careflow/contracts";
import { Card } from "@careflow/ui";
import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, useState } from "react";

import { api } from "@/lib/api";

type AskResponse = Schema<"AskResponse">;

/**
 * "Ask the clinic" (M2-07 UI; the answering agent is M2-09): questions about the clinic, answered
 * from its documents with citation chips. A medical question gets the fixed safe message
 * (`refused_medical`: never advice, safety rule 2), and an unknown one says so (`dont_know`).
 */
export function AskPanel() {
  const t = useTranslations("ask");
  const language = useLocale() === "ar" ? "ar" : "en";
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [state, setState] = useState<"idle" | "sending" | "unavailable">("idle");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!question.trim()) return;
    setState("sending");
    try {
      const { data } = await api.POST("/v1/companion/ask", { body: { question: question.trim(), language } });
      setAnswer(data ?? null);
      setState(data ? "idle" : "unavailable");
    } catch {
      setState("unavailable");
    }
  }

  return (
    <Card title={t("title")}>
      <form className="flex flex-col gap-2" onSubmit={submit}>
        <label className="flex flex-col gap-2 font-semibold">
          {t("label")}
          <textarea
            value={question}
            maxLength={500}
            onChange={(event) => setQuestion(event.target.value)}
            rows={2}
            className="rounded-control border-2 border-border bg-surface p-3 text-body font-normal"
          />
        </label>
        <button
          type="submit"
          className="min-h-touch rounded-control bg-primary px-6 font-semibold text-on-primary disabled:opacity-50"
          disabled={state === "sending" || !question.trim()}
        >
          {state === "sending" ? t("sending") : t("send")}
        </button>
      </form>
      {state === "unavailable" ? (
        <p role="alert" className="mt-3">
          {t("unavailable")}
        </p>
      ) : null}
      {answer ? (
        <div className="mt-4 flex flex-col gap-2" aria-live="polite" data-testid="ask-answer">
          {answer.refused_medical ? (
            <p className="font-semibold">{t("refused")}</p>
          ) : answer.dont_know ? (
            <p>{t("dontKnow")}</p>
          ) : (
            <p dir="auto">{answer.answer}</p>
          )}
          {answer.citations.length > 0 ? (
            <ul className="flex flex-wrap gap-2" aria-label={t("sources")}>
              {answer.citations.map((citation) => (
                <li key={citation.chunk_id} className="rounded-control border-2 border-border px-2 text-label" dir="auto">
                  {citation.title} <bdi dir="ltr" className="text-ink-muted">({citation.doc_id})</bdi>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}
