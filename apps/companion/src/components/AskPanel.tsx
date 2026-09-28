"use client";

import type { Schema } from "@careflow/contracts";
import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, useState } from "react";

import { api } from "@/lib/api";

import { CompanionIcon } from "./CompanionIcon";

type AskResponse = Schema<"AskResponse">;

/**
 * "Ask the clinic" (M2-07 UI, M2-09 agent): questions about the clinic, answered from its handbook
 * or the patient's own queue, with citation chips. A medical question gets the fixed safe message
 * `safety.rag.refusal` (`refused_medical`: never advice, safety rule 2) and the nurse is flagged;
 * an unknown one says so (`dont_know`). A 503 means the question could not be handled, so the
 * patient is sent to the desk.
 */
export function AskPanel() {
  const t = useTranslations("ask");
  const tSafety = useTranslations("safety.rag");
  const language = useLocale() === "ar" ? "ar" : "en";
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [state, setState] = useState<"idle" | "sending" | "unavailable">("idle");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!question.trim()) return;
    setState("sending");
    setAnswer(null);
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 30_000);
    try {
      const { data } = await api.POST("/v1/companion/ask", { signal: controller.signal, body: { question: question.trim(), language } });
      setAnswer(data ?? null);
      setState(data ? "idle" : "unavailable");
    } catch {
      setState("unavailable");
    } finally {
      window.clearTimeout(timer);
    }
  }

  return (
    <section className="companion-panel companion-ask-panel" id="ask-clinic">
      <div className="companion-section-title"><CompanionIcon name="message" /><h2>{t("title")}</h2></div>
      <p className="companion-muted">{t("intro")}</p>
      <div className="companion-quick-prompts">
        {(["parking", "hours", "pharmacy"] as const).map((key) => <button key={key} type="button" onClick={() => setQuestion(t(`${key}Question`))} disabled={state === "sending"}>{t(key)}</button>)}
      </div>
      <form className="companion-form" onSubmit={submit}>
        <label className="flex flex-col gap-2 font-semibold">
          {t("label")}
          <textarea
            value={question}
            maxLength={500}
            onChange={(event) => setQuestion(event.target.value)}
            rows={3}
            placeholder={t("placeholder")}
            className="companion-input"
          />
        </label>
        <button
          type="submit"
          className="companion-primary"
          disabled={state === "sending" || !question.trim()}
        >
          {state === "sending" ? t("sending") : t("send")}
        </button>
      </form>
      <p className="companion-field-hint">{t("hint")}</p>
      {state === "unavailable" ? (
        <p role="alert" className="mt-3">
          {t("unavailable")}
        </p>
      ) : null}
      {answer ? (
        <div className="companion-answer" aria-live="polite" data-testid="ask-answer">
          {answer.refused_medical ? (
            <p className="font-semibold">{tSafety("refusal")}</p>
          ) : answer.dont_know ? (
            <p>{t("dontKnow")}</p>
          ) : (
            <p dir="auto">{answer.answer}</p>
          )}
          {answer.citations.length > 0 ? (
            <ul className="flex flex-wrap gap-2" aria-label={t("sources")}>
              {answer.citations.map((citation) => (
                <li key={citation.chunk_id} className="companion-citation" dir="auto">
                  {citation.title} <bdi dir="ltr" className="text-ink-muted">({citation.doc_id})</bdi>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
