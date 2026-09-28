"use client";

import type { Schema } from "@careflow/contracts";
import { Card } from "@careflow/ui";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { createCareflowClientFor } from "@/lib/api";
import { currentIntake, isIntakeRevision } from "@/lib/phoneVisit";
import { intakeGuidance } from "@/lib/intakeGuidance";
import { type AnswerKey, quickAnswers } from "@/lib/quickAnswers";
import { clearSession } from "@/lib/session";
import { HoldToTalk } from "./HoldToTalk";
import { FeelsWorse } from "./FeelsWorse";
import { PatientReceipt } from "./PatientReceipt";

import { CompanionIcon } from "./CompanionIcon";

type Question = Schema<"IntakeQuestion">;

const BUTTON = "companion-choice";
const PRIMARY = "companion-primary";

/**
 * The intake on the phone after the QR hand-over (M2-06): the same questions, the same graph and
 * the same rules as the kiosk (safety rules 1 and 3), answered by tap or text with
 * POST /v1/sessions/{id}/turns and the phone-channel token.
 */
export function PhoneIntake({
  sessionId,
  token,
  language,
  display,
  initialQuestion,
  initialRevision,
  initialState,
  onReconnect,
  childSession = false,
}: {
  sessionId: string;
  token: string;
  language: "en" | "ar";
  display: string;
  initialQuestion: Question | null;
  initialRevision: number | null;
  initialState: Schema<"SessionState">;
  onReconnect: () => void;
  childSession?: boolean;
}) {
  const t = useTranslations("intake");
  const tClaim = useTranslations("claim");
  const router = useRouter();
  const client = useMemo(() => createCareflowClientFor(token), [token]);
  const [question, setQuestion] = useState<Question | null>(initialQuestion);
  const [revision, setRevision] = useState(initialRevision);
  const [visitState, setVisitState] = useState(initialState);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [heard, setHeard] = useState("");
  const [failed, setFailed] = useState(false);
  const [recovery, setRecovery] = useState<"needed" | "expired" | null>(null);
  const busy = useRef(false);
  const answerInput = useRef<HTMLTextAreaElement>(null);
  const needsStatus = visitState === "called" || visitState === "arrived";

  useEffect(() => {
    if (needsStatus) router.replace("/status");
  }, [needsStatus, router]);

  async function recover() {
    const current = await currentIntake(sessionId, token);
    if (current.status === "expired") {
      setRecovery("expired");
    } else if (current.status === "unavailable") {
      setRecovery("needed");
    } else {
      const advanced = current.revision !== revision;
      setQuestion(current.question);
      setRevision(current.revision);
      setVisitState(current.state);
      setRecovery(null);
      setFailed(!advanced);
      if (advanced) setText("");
    }
  }

  async function send(inputMode: "tap" | "text", answer: string) {
    if (!answer.trim() || busy.current || voiceBusy || recovery || revision === null) return;
    busy.current = true;
    setSending(true);
    setFailed(false);
    try {
      const { data, response } = await client.POST("/v1/sessions/{session_id}/turns", {
        signal: AbortSignal.timeout(40_000),
        params: { path: { session_id: sessionId } },
        body: { input_mode: inputMode, text: answer.trim(), language, expected_intake_revision: revision },
      });
      if (response.status === 401 || response.status === 403 || response.status === 404) {
        clearSession();
        setRecovery("expired");
      } else if (!data || !isIntakeRevision(data.intake_revision) || (!data.done && !data.next_question)) {
        await recover();
      } else {
        setText("");
        setQuestion(data.done ? null : data.next_question!);
        setRevision(data.done ? null : data.intake_revision!);
        setVisitState(data.done ? "awaiting_nurse" : "intake");
      }
    } catch {
      // The server may have accepted an answer whose response was lost. Read its checkpoint;
      // every retry also carries the server revision so an in-flight answer cannot be duplicated.
      await recover();
    } finally {
      busy.current = false;
      setSending(false);
    }
  }

  function reconcileVoice() {
    busy.current = true; setSending(true);
    void recover().finally(() => { busy.current = false; setSending(false); });
  }

  async function retryRecovery() {
    if (busy.current) return;
    busy.current = true;
    setSending(true);
    try { await recover(); } finally { busy.current = false; setSending(false); }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void send("text", text);
  }

  if (recovery === "expired") {
    return <Card><p role="alert">{tClaim("expired")}</p>
      <button type="button" onClick={onReconnect} className={PRIMARY}>{tClaim("enterCode")}</button>
    </Card>;
  }

  if (needsStatus) return <p role="status">{t("openingStatus")}</p>;

  if (!question) {
    return (
      <Card className="companion-panel companion-complete" title={tClaim.rich("welcome", { initials: display, ltr: (chunks) => <bdi dir="ltr">{chunks}</bdi> })}>
        <span className="companion-hero-icon"><CompanionIcon name="check" /></span>
        <p className="companion-eyebrow">{t("completeLabel")}</p>
        <p className="companion-complete-copy">{t("done")}</p>
        <p className="companion-muted">{t("reviewHelp")}</p>
        <Link href="/status" className={PRIMARY + " inline-flex items-center"}>
          {t("status")}
        </Link>
      </Card>
    );
  }

  const quick = quickAnswers(question.key ?? null);
  const guidance = intakeGuidance(question.key ?? null);
  const label = (key: AnswerKey) => t(`answers.${key}`);
  return (
    <div className="companion-intake">
      <PatientReceipt compact />
      <section className="companion-panel companion-question-panel">
      <div className="companion-question-label"><CompanionIcon name="message" /><span>{t("assistantLabel")}</span></div>
      {question.notice ? (
        <p role="status" className="companion-notice">
          {question.notice}
        </p>
      ) : null}
      <h1 className="companion-question mb-5 text-[28px] font-semibold leading-tight tracking-tight" dir="auto" aria-live="polite">
        {question.text}
      </h1>
      <p className="sr-only" id="question-help">{t(`guidance.${guidance}.help`)}</p>
      {question.key !== "chief_complaint" && quick.kind === "choices" ? (
        <div className="companion-choices">
          {quick.answers.map((key) => (
            <button key={key} type="button" className={BUTTON} disabled={sending || voiceBusy || recovery !== null} onClick={() => void send("tap", label(key))}>
              {label(key)}
            </button>
          ))}
        </div>
      ) : null}
      {quick.kind === "scale" ? (
        <div className="companion-pain-scale" role="group" aria-label={t("scale")}>
          {Array.from({ length: 11 }, (_, value) => (
            <button key={value} type="button" className={BUTTON} disabled={sending || voiceBusy || recovery !== null} onClick={() => void send("tap", String(value))}>
              <bdi dir="ltr">{value}</bdi>
            </button>
          ))}
        </div>
      ) : null}
      <HoldToTalk session={{ sessionId, token, language }} disabled={sending || recovery !== null}
        onBusyChange={setVoiceBusy} onTranscript={setHeard}
        onQuestion={() => {
          // The voice service posts the turn itself. Read the authoritative revision before
          // allowing another typed or spoken answer; never replay the transcript as a text turn.
          reconcileVoice();
        }} onUncertain={reconcileVoice} />
      {heard ? <p className="companion-voice-transcript"><strong>{t("voiceHeard")}</strong><span dir="auto">{heard}</span></p> : null}
      <form className="companion-form" onSubmit={submit}>
        <label className="flex flex-col gap-2 text-label font-semibold">
          {t("typeAnswer")}
          <textarea
            ref={answerInput}
            value={text}
            maxLength={1000}
            onChange={(event) => setText(event.target.value)}
            rows={3}
            dir="auto"
            aria-describedby="question-help"
            placeholder={question.key === "chief_complaint" ? t("answerPlaceholder") : t(`guidance.${guidance}.example`)}
            disabled={sending || voiceBusy || recovery !== null}
            className="companion-input"
          />
        </label>
        {question.key === "chief_complaint" ? <div className="phone-answer-examples">
          <p className="mb-2 text-xs text-ink-muted">{t("examples.caption")}</p>
          <div className="flex flex-wrap gap-2" role="group" aria-label={t("examples.group")}>
            {(["ankle", "headache", "results"] as const).map((example) => <button key={example} type="button"
              className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-1.5 text-xs text-ink disabled:opacity-50"
              aria-label={`${t("examples.badge")} ${t(`examples.${example}.label`)}`}
              disabled={sending || voiceBusy || recovery !== null}
              onClick={() => { setText(t(`examples.${example}.text`)); answerInput.current?.focus(); }}>
              <span className="rounded bg-canvas px-1 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-ink-muted">{t("examples.badge")}</span><span>{t(`examples.${example}.label`)}</span>
            </button>)}
          </div>
        </div> : null}
        <button type="submit" className={PRIMARY} disabled={sending || voiceBusy || recovery !== null || !text.trim()}>
          {sending ? t("sending") : t("send")}
        </button>
      </form>
      {sending ? <p className="companion-processing" role="status"><span className="companion-spinner" aria-hidden="true" />{t("processingAnswer")}</p> : null}
      {recovery === "needed" ? (
        <div>
          <p role="alert">{t("recoveryNeeded")}</p>
          <button type="button" className={BUTTON} disabled={sending} onClick={() => void retryRecovery()}>
            {t("retryRecovery")}
          </button>
        </div>
      ) : null}
      {failed ? (
        <p role="alert" className="companion-notice companion-error">
          {t("failed")}
        </p>
      ) : null}
      </section>
      <FeelsWorse childSession={childSession} />
    </div>
  );
}
