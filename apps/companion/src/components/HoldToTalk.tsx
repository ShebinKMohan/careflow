"use client";

import { useTranslations } from "next-intl";
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { readSession } from "@/lib/session";
import { createVoiceClient, voiceEnabled, type VoiceClient, type VoiceSession } from "@/lib/voice";
import type { Schema } from "@careflow/contracts";
type IntakeQuestion = Schema<"IntakeQuestion">;
import { CompanionIcon } from "./CompanionIcon";

type Phase = "idle" | "connecting" | "listening" | "processing";
type Props = {
  session: VoiceSession;
  disabled?: boolean;
  onTranscript(text: string): void;
  onQuestion(question: IntakeQuestion | null): void;
  onBusyChange?(busy: boolean): void;
  onUncertain?(): void;
  /** Injectable for deterministic permission/capture tests. */
  client?: VoiceClient;
};

function isTyping(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && Boolean(target.closest("input, textarea, select, summary, [contenteditable], [role=dialog], [role=alertdialog], button:not([data-hold-to-talk])"));
}

/** The microphone exists only while held; release commits one utterance, never ambient audio. */
export function HoldToTalk(props: Props) {
  const t = useTranslations("holdToTalk");
  const helpId = useId();
  const voice = useMemo(() => props.client ?? createVoiceClient(), [props.client]);
  const latest = useRef(props);
  useLayoutEffect(() => { latest.current = props; });
  const held = useRef(false);
  const mounted = useRef(true);
  const attempt = useRef(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const phaseRef = useRef<Phase>("idle");
  const [phase, setPhase] = useState<Phase>("idle");
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<"tryAgain" | "noSpeech" | "failed" | null>(null);
  const allowed = Boolean(props.client || voiceEnabled()) && readSession()?.sessionId === props.session.sessionId && Boolean(readSession()?.voiceConsent);
  const secure = Boolean(props.client || (typeof window !== "undefined" && window.isSecureContext));
  const enabled = allowed && secure;

  const updatePhase = useCallback((next: Phase) => {
    phaseRef.current = next;
    if (mounted.current) setPhase(next);
    latest.current.onBusyChange?.(next !== "idle");
  }, []);
  const stopTimer = useCallback(() => {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
  }, []);
  const cancel = useCallback(() => {
    attempt.current += 1;
    held.current = false;
    stopTimer();
    voice.abort();
    updatePhase("idle");
  }, [stopTimer, updatePhase, voice]);
  const release = useCallback(() => {
    if (!held.current) return;
    held.current = false;
    stopTimer();
    const sent = voice.finishUtterance();
    updatePhase(sent ? "processing" : "idle");
    if (!sent) {
      attempt.current += 1;
      if (mounted.current) setError("tryAgain");
    }
  }, [stopTimer, updatePhase, voice]);

  const begin = useCallback(async () => {
    if (!enabled || latest.current.disabled || held.current || phaseRef.current === "processing") return;
    held.current = true;
    const current = ++attempt.current;
    setError(null);
    setSeconds(0);
    updatePhase("connecting");
    try {
      await voice.start({ ...latest.current.session, inputMode: "push_to_talk" }, {
        onTranscript: (text) => { if (current === attempt.current) latest.current.onTranscript(text); },
        onQuestion: (question) => {
          if (current !== attempt.current) return;
          updatePhase("idle");
          latest.current.onQuestion(question);
        },
        onError: (code) => {
          if (current !== attempt.current) return;
          const mayHaveSubmitted = phaseRef.current === "processing";
          cancel();
          if (mayHaveSubmitted) latest.current.onUncertain?.();
          if (mounted.current) setError(code === "no_speech" ? "noSpeech" : "failed");
        },
      });
      if (!held.current || current !== attempt.current || !mounted.current) return;
      updatePhase("listening");
      const started = Date.now();
      timer.current = setInterval(() => {
        const elapsed = Math.floor((Date.now() - started) / 1000);
        setSeconds(elapsed);
        if (elapsed >= 29) release();
      }, 250);
    } catch {
      if (current !== attempt.current || !mounted.current) return;
      cancel();
      setError("failed");
    }
  }, [cancel, enabled, release, updatePhase, voice]);

  useEffect(() => {
    if (phase !== "processing") return;
    const timeout = window.setTimeout(() => {
      cancel(); setError("failed"); latest.current.onUncertain?.();
    }, 45_000);
    return () => window.clearTimeout(timeout);
  }, [cancel, phase]);

  useEffect(() => {
    mounted.current = true;
    const down = (event: KeyboardEvent) => {
      if (event.code !== "Space" || event.repeat || event.altKey || event.ctrlKey || event.metaKey || isTyping(event.target)) return;
      if (!enabled || latest.current.disabled || phaseRef.current === "processing") return;
      event.preventDefault();
      void begin();
    };
    const up = (event: KeyboardEvent) => {
      if (event.code !== "Space" || !held.current) return;
      event.preventDefault();
      release();
    };
    const hide = () => { if (document.hidden) cancel(); };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", cancel);
    window.addEventListener("pointerup", release);
    document.addEventListener("visibilitychange", hide);
    return () => {
      mounted.current = false;
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", cancel);
      window.removeEventListener("pointerup", release);
      document.removeEventListener("visibilitychange", hide);
      cancel();
    };
  }, [begin, cancel, enabled, release]);

  if (!allowed) return null;
  if (!secure) return <p className="companion-notice">{t("secureContext")}</p>;
  return <section className="hold-to-talk relative" data-phase={phase} aria-label={t("title")}>
    <button type="button" data-hold-to-talk
      className="hold-to-talk-button flex min-h-16 w-full touch-none select-none flex-col items-center justify-center gap-2 rounded-xl p-2 text-base font-semibold text-ink disabled:opacity-60"
      disabled={props.disabled || phase === "processing"} aria-pressed={phase === "listening"}
      aria-describedby={helpId}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.currentTarget.setPointerCapture?.(event.pointerId);
        void begin();
      }}
      onPointerUp={release} onPointerCancel={cancel} onLostPointerCapture={() => { if (held.current) cancel(); }}
      onContextMenu={(event) => event.preventDefault()}>
      <span className={`hold-to-talk-mic flex h-16 w-16 items-center justify-center rounded-full text-white shadow-sm ${phase === "listening" ? "bg-danger" : "bg-primary"}`}>
        {phase === "listening" ? <span className="hold-to-talk-wave flex h-7 items-center gap-1" aria-hidden="true">{[12, 22, 28, 18, 10].map((height, index) => <span key={index} className="w-1 rounded-full bg-current motion-safe:animate-pulse" style={{ height, animationDelay: `${index * 120}ms` }} />)}</span> : <CompanionIcon name="voice" className="h-7 w-7" />}
      </span>
      <span className="hold-to-talk-label flex items-center gap-2">{t(phase)}{phase === "listening" ? <span className="hold-to-talk-timer text-sm tabular-nums" dir="ltr">{seconds}s</span> : null}</span>
    </button>
    <div className="hold-to-talk-footer mt-2 flex items-center justify-center gap-3 text-xs text-ink-muted">
      <p id={helpId} role="status" className="hold-to-talk-status flex items-center gap-1.5">
        {phase === "listening" || phase === "connecting" ? <span className="h-1.5 w-1.5 rounded-full bg-danger" aria-hidden="true" /> : <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true"><path d="M9 9V5a3 3 0 0 1 6 0v7m-6 1a3 3 0 0 0 5 2M6 11v1a6 6 0 0 0 10 4m2-3v-2M12 18v4m-4 0h8M3 3l18 18" /></svg>}
        {t(phase === "listening" ? "releaseHint" : phase === "connecting" ? "keepHolding" : "micOff")}
      </p>
      <span className="hold-to-talk-keyboard hidden items-center gap-1 sm:inline-flex"><span>{t("holdKey")}</span><kbd className="rounded border border-line bg-surface px-1.5 py-0.5 font-sans text-[10px]">{t("spaceKey")}</kbd></span>
      <details className="hold-to-talk-info">
        <summary className="flex h-7 w-7 cursor-pointer list-none items-center justify-center rounded-full hover:bg-canvas focus-visible:outline-2 focus-visible:outline-primary" aria-label={t("infoLabel")} title={t("infoLabel")}>
          <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 11v6m0-10v.01" /></svg>
        </summary>
        <div className="hold-to-talk-info-content absolute inset-x-0 z-10 mt-2 rounded-xl border border-line bg-surface p-3 text-start text-xs leading-relaxed shadow-sm">
          <p>{t("hint")}</p><p className="mt-2">{t("privacy")}</p>
        </div>
      </details>
    </div>
    {error ? <p role="alert" className="hold-to-talk-error mt-2 text-sm font-semibold text-danger">{t(error)}</p> : null}
  </section>;
}
