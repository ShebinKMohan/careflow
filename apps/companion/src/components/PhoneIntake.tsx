"use client";

import type { Schema } from "@careflow/contracts";
import { Card } from "@careflow/ui";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { createCareflowClientFor } from "@/lib/api";
import { currentIntake, isIntakeRevision } from "@/lib/phoneVisit";
import { type AnswerKey, quickAnswers } from "@/lib/quickAnswers";
import { clearSession } from "@/lib/session";

type Question = Schema<"IntakeQuestion">;

const BUTTON = "min-h-touch rounded-control border-2 border-primary px-4 text-body font-semibold text-primary disabled:opacity-50";
const PRIMARY = "min-h-touch rounded-control bg-primary px-6 text-title font-semibold text-on-primary disabled:opacity-50";

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
}: {
  sessionId: string;
  token: string;
  language: "en" | "ar";
  display: string;
  initialQuestion: Question | null;
  initialRevision: number | null;
  initialState: Schema<"SessionState">;
  onReconnect: () => void;
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
  const [failed, setFailed] = useState(false);
  const [recovery, setRecovery] = useState<"needed" | "expired" | null>(null);
  const busy = useRef(false);
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
    if (!answer.trim() || busy.current || recovery || revision === null) return;
    busy.current = true;
    setSending(true);
    setFailed(false);
    try {
      const { data, response } = await client.POST("/v1/sessions/{session_id}/turns", {
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
      <Card title={tClaim.rich("welcome", { initials: display, ltr: (chunks) => <bdi dir="ltr">{chunks}</bdi> })}>
        <p className="mb-4 text-title">{t("done")}</p>
        <Link href="/status" className={PRIMARY + " inline-flex items-center"}>
          {t("status")}
        </Link>
      </Card>
    );
  }

  const quick = quickAnswers(question.key ?? null);
  const label = (key: AnswerKey) => t(`answers.${key}`);
  return (
    <div className="flex flex-col gap-4">
      <p className="text-label text-ink-muted">{t.rich("greeting", { initials: display, ltr: (chunks) => <bdi dir="ltr">{chunks}</bdi> })}</p>
      {question.notice ? (
        <p role="status" className="font-semibold">
          {question.notice}
        </p>
      ) : null}
      <h2 className="text-title font-semibold" dir="auto">
        {question.text}
      </h2>
      {quick.kind === "choices" ? (
        <div className="flex flex-wrap gap-2">
          {quick.answers.map((key) => (
            <button key={key} type="button" className={BUTTON} disabled={sending || recovery !== null} onClick={() => void send("tap", label(key))}>
              {label(key)}
            </button>
          ))}
        </div>
      ) : null}
      {quick.kind === "scale" ? (
        <div className="grid grid-cols-6 gap-2" role="group" aria-label={t("scale")}>
          {Array.from({ length: 11 }, (_, value) => (
            <button key={value} type="button" className={BUTTON} disabled={sending || recovery !== null} onClick={() => void send("tap", String(value))}>
              <bdi dir="ltr">{value}</bdi>
            </button>
          ))}
        </div>
      ) : null}
      <form className="flex flex-col gap-2" onSubmit={submit}>
        <label className="flex flex-col gap-2 text-label font-semibold">
          {t("typeAnswer")}
          <textarea
            value={text}
            maxLength={1000}
            onChange={(event) => setText(event.target.value)}
            rows={3}
            className="rounded-control border-2 border-border bg-surface p-3 text-body"
          />
        </label>
        <button type="submit" className={PRIMARY} disabled={sending || recovery !== null || !text.trim()}>
          {sending ? t("sending") : t("send")}
        </button>
      </form>
      {recovery === "needed" ? (
        <div>
          <p role="alert">{t("recoveryNeeded")}</p>
          <button type="button" className={BUTTON} disabled={sending} onClick={() => void retryRecovery()}>
            {t("retryRecovery")}
          </button>
        </div>
      ) : null}
      {failed ? (
        <p role="alert" className="font-semibold">
          {t("failed")}
        </p>
      ) : null}
    </div>
  );
}
