"use client";

import { Card } from "@careflow/ui";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { type FormEvent, useEffect, useMemo, useState } from "react";

import { patientApi } from "@/lib/api";
import { type AnswerKey, quickAnswers } from "@/lib/quickAnswers";
import { type IntakeQuestion, useKiosk } from "@/lib/session";
import { createVoiceClient, voiceEnabled } from "@/lib/voice";

type InputMode = "tap" | "text" | "voice";

const ANSWER = "min-h-touch rounded-control border-2 border-border bg-surface px-5 text-title font-semibold text-ink";
const PRIMARY = "min-h-touch rounded-control bg-primary px-6 text-title font-semibold text-on-primary disabled:opacity-60";
// 0-10 in one row on a kiosk-width screen: each button stays at least 56 px wide.
const SCALE = "min-h-touch min-w-0 rounded-control border-2 border-border bg-surface text-title font-semibold text-ink";

/**
 * One question at a time, in large text, with three ways to answer that are always on screen:
 * tap choices for the question's key, a text box and (when enabled) the mic. Every answer goes to
 * POST /v1/sessions/{id}/turns; when there is no next question the patient moves on to vitals.
 */
export function IntakeFlow() {
  const t = useTranslations("intake");
  const router = useRouter();
  const { patient, setQuestion, onWipe } = useKiosk();
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [failed, setFailed] = useState(false);
  const [listening, setListening] = useState(false);
  const token = patient?.token;
  const client = useMemo(() => (token ? patientApi(token) : null), [token]);
  const voice = useMemo(() => createVoiceClient(), []);

  useEffect(() => onWipe(() => voice.abort()), [onWipe, voice]);
  useEffect(() => {
    if (patient && patient.question === null) router.push("/vitals");
  }, [patient, router]);

  if (!patient || !client) {
    return (
      <Card>
        <p className="mb-4">{t("needsCheckin")}</p>
        <Link href="/checkin" className="inline-flex min-h-touch items-center font-semibold text-primary underline">
          {t("checkinLink")}
        </Link>
      </Card>
    );
  }
  const question = patient.question;
  if (!question) return null;

  async function send(inputMode: InputMode, answer: string) {
    if (!patient || !client || !answer.trim()) return;
    setSending(true);
    setFailed(false);
    const { data } = await client.POST("/v1/sessions/{session_id}/turns", {
      params: { path: { session_id: patient.sessionId } },
      body: { input_mode: inputMode, text: answer.trim(), language: patient.language },
    });
    setSending(false);
    if (!data) {
      setFailed(true);
      return;
    }
    setText("");
    const next: IntakeQuestion | null =
      data.next_question === undefined
        ? data.done
          ? null
          : { question_id: data.turn_id, text: data.assistant_text, key: null, notice: null }
        : data.next_question;
    setQuestion(next);
  }

  function submitText(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void send("text", text);
  }

  async function listen() {
    setListening(true);
    await voice.start((heard) => void send("voice", heard));
    setListening(false);
  }

  const answers = quickAnswers(question.key ?? null);
  const label = (answer: AnswerKey) => t(`answers.${answer}`);

  return (
    <div className="flex flex-col gap-6">
      <p className="text-label text-ink-muted">{t("greeting", { initials: patient.display })}</p>
      {question.notice ? (
        <p role="status" className="rounded-card border-2 border-border bg-secondary p-gutter text-title text-on-secondary">
          {question.notice}
        </p>
      ) : null}
      <h2 className="text-title font-bold" aria-live="polite">
        {question.text}
      </h2>
      {answers.kind === "choices" ? (
        <div className="grid gap-3 sm:grid-cols-3" role="group" aria-label={t("tapLabel")}>
          {answers.answers.map((answer) => (
            <button key={answer} type="button" className={ANSWER} disabled={sending} onClick={() => void send("tap", label(answer))}>
              {label(answer)}
            </button>
          ))}
        </div>
      ) : null}
      {answers.kind === "scale" ? (
        <div className="flex flex-col gap-2" role="group" aria-label={t("scaleLabel")}>
          <div className="grid grid-cols-4 gap-2 sm:grid-cols-6 md:grid-cols-11">
            {Array.from({ length: answers.max - answers.min + 1 }, (_, i) => String(answers.min + i)).map((value) => (
              <button key={value} type="button" className={SCALE} disabled={sending} onClick={() => void send("tap", value)}>
                {value}
              </button>
            ))}
          </div>
          <p className="text-label text-ink-muted">{t("scaleHint")}</p>
        </div>
      ) : null}
      <form className="flex flex-col gap-3" onSubmit={submitText}>
        <label className="flex flex-col gap-2 text-label font-semibold">
          {t("typeLabel")}
          <textarea
            value={text}
            onChange={(event) => setText(event.target.value)}
            maxLength={1000}
            rows={3}
            className="rounded-control border-2 border-border bg-surface p-4 text-body"
          />
        </label>
        <button type="submit" className={PRIMARY} disabled={sending || !text.trim()}>
          {sending ? t("sending") : t("send")}
        </button>
      </form>
      {voiceEnabled() ? (
        <button type="button" className={PRIMARY} disabled={sending || listening} onClick={() => void listen()}>
          {listening ? t("listening") : t("mic")}
        </button>
      ) : null}
      {failed ? (
        <p role="alert" className="font-semibold text-danger">
          {t("failed")}
        </p>
      ) : null}
    </div>
  );
}
