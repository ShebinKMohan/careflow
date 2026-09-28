"use client";

import { Card } from "@careflow/ui";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { patientApi } from "@/lib/api";
import { type AnswerKey, quickAnswers } from "@/lib/quickAnswers";
import { type IntakeQuestion, useKiosk } from "@/lib/session";
import { ContinueOnPhone } from "@/components/ContinueOnPhone";
import { MESSAGES } from "@/i18n/messages";
import { HoldToTalk } from "./HoldToTalk";
import { CameraPreview } from "./CameraPreview";
import { KioskIcon } from "./KioskIcon";
import { PatientReceipt } from "./PatientReceipt";

type InputMode = "tap" | "text" | "voice";

const ANSWER = "min-h-touch rounded-control border-2 border-border bg-surface px-5 text-title font-semibold text-ink";
// 0-10 in one row on a kiosk-width screen: each button stays at least 56 px wide.
const SCALE = "min-h-touch min-w-0 rounded-control border-2 border-border bg-surface text-title font-semibold text-ink";

/**
 * One question at a time, in large text, with three ways to answer that are always on screen:
 * tap choices for the question's key, a text box and (when enabled) the mic. Every answer goes to
 * POST /v1/sessions/{id}/turns; when there is no next question the patient moves on to vitals.
 * A question the patient asks along the way gets its reply as the notice (M2-10); "ask this on
 * your phone" highlights "Continue on your phone".
 */
export function IntakeFlow() {
  const t = useTranslations("intake");
  const adaptive = useTranslations("adaptiveIntake");
  const router = useRouter();
  const { patient, setQuestion } = useKiosk();
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [failed, setFailed] = useState(false);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [heard, setHeard] = useState<string | null>(null);
  const [seconds, setSeconds] = useState(0);
  const answerField = useRef<HTMLTextAreaElement>(null);
  const token = patient?.token;
  const client = useMemo(() => (token ? patientApi(token) : null), [token]);
  useEffect(() => {
    if (!sending) return;
    const start = Date.now();
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [sending]);
  useEffect(() => {
    // A child skips the adult sensor steps (M2-04): a nurse checks temperature and breathing.
    if (patient && patient.question === null) router.push(patient.patientType === "child" ? "/done" : "/vitals");
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
    if (!patient || !client || !answer.trim() || sending || voiceBusy) return;
    setSending(true);
    setSeconds(0);
    setFailed(false);
    const result = await client.POST("/v1/sessions/{session_id}/turns", {
      params: { path: { session_id: patient.sessionId } },
      body: { input_mode: inputMode, text: answer.trim(), language: patient.language },
    }).catch(() => null);
    setSending(false);
    const data = result?.data;
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
    setQuestion(next, patient.sessionId);
  }

  function submitText(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void send("text", text);
  }

  const answers = quickAnswers(question.key ?? null);
  const label = (answer: AnswerKey) => t(`answers.${answer}`);
  const isOpen = question.key === "chief_complaint" || answers.kind === "none";
  const exampleKey = question.key === "chief_complaint" ? "complaint" : question.key?.includes("onset") || question.key?.includes("last_known") ? "onset" : question.key === "pain_location" ? "location" : "general";
  const examples = Object.values(MESSAGES[patient.language].adaptiveIntake[exampleKey]);

  return (
    <div className="adaptive-intake">
      <div className="adaptive-intake-heading"><span className="assistant-brand"><span className="assistant-signal" aria-hidden="true"><i /><i /><i /></span>{adaptive("eyebrow")}</span><Link className="reception-back" href="/checkin"><KioskIcon name="back" />{adaptive("back")}</Link></div>
      <PatientReceipt compact />
      <div className="adaptive-intake-main">
      {question.notice ? (
        <p role="status" className="rounded-card border-2 border-border bg-secondary p-gutter text-title text-on-secondary">
          {question.notice}
        </p>
      ) : null}
      <div className="intake-question"><h1 aria-live="polite">
        {question.text}
      </h1></div>
      {answers.kind === "choices" && !isOpen ? (
        <div className="grid gap-3 sm:grid-cols-3" role="group" aria-label={t("tapLabel")}>
          {answers.answers.map((answer) => (
            <button key={answer} type="button" className={ANSWER} disabled={sending || voiceBusy} onClick={() => void send("tap", label(answer))}>
              {label(answer)}
            </button>
          ))}
        </div>
      ) : null}
      <HoldToTalk
        session={{ sessionId: patient.sessionId, token: patient.token, language: patient.language }}
        disabled={sending}
        onBusyChange={setVoiceBusy}
        onTranscript={setHeard}
        onQuestion={(next) => setQuestion(next, patient.sessionId)}
      />
      {heard ? <p className="text-body" data-testid="heard">{t("heard", { text: heard })}</p> : null}
      {answers.kind === "scale" ? (
        <div className="flex flex-col gap-2" role="group" aria-label={t("scaleLabel")}>
          <div className="intake-scale-grid grid grid-cols-4 gap-2 sm:grid-cols-6 md:grid-cols-11">
            {Array.from({ length: answers.max - answers.min + 1 }, (_, i) => String(answers.min + i)).map((value) => (
              <button key={value} type="button" className={SCALE} disabled={sending || voiceBusy} onClick={() => void send("tap", value)}>
                {value}
              </button>
            ))}
          </div>
          <p className="text-label text-ink-muted">{t("scaleHint")}</p>
        </div>
      ) : null}
      <div className="intake-response">
      <label className="intake-answer-label" htmlFor="intake-answer">{t("typeLabel")}</label>
      <form className="intake-composer" onSubmit={submitText}>
          <textarea
            ref={answerField}
            id="intake-answer"
            value={text}
            onChange={(event) => setText(event.target.value)}
            maxLength={1000}
            rows={2}
            placeholder={adaptive("placeholder")}
            disabled={sending || voiceBusy}
          />
        <button type="submit" className="intake-send" disabled={sending || voiceBusy || !text.trim()} aria-label={sending ? t("sending") : t("send")}>
          <KioskIcon name="arrow" /><span className="sr-only">{sending ? t("sending") : t("send")}</span>
        </button>
      </form>
      </div>
      {isOpen && <div className="answer-examples"><p>{adaptive("examplesTitle")}</p><div>{examples.map((example) => <button type="button" key={example} disabled={sending || voiceBusy} onClick={() => { setText(example); answerField.current?.focus(); }} aria-label={adaptive("examplesLabel", { example })}><span className="example-badge" aria-hidden="true">{adaptive("exampleBadge")}</span><span>{example}</span></button>)}</div></div>}
      {sending && <p className="intake-processing" role="status">{adaptive("processing", { seconds })}<small>{adaptive("processingHint")}</small></p>}
      {failed ? (
        <p role="alert" className="font-semibold text-danger">
          {t("failed")}
        </p>
      ) : null}
      </div>
      <div className="intake-tools"><CameraPreview /><ContinueOnPhone compact highlight={question.notice_kind === "ask_on_phone"} /></div>
    </div>
  );
}
