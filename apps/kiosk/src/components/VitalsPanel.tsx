"use client";

import type { Schema } from "@careflow/contracts";
import { BigButton } from "@careflow/ui";
import { useTranslations } from "next-intl";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";

type Vitals = Schema<"LatestVitals">;
type State = "idle" | "measuring" | "complete" | "failed";

export const POLL_MS = 1_000;
export const MEASUREMENT_MS = 10_000;
const POLL_LIMIT = 30;

export interface VitalsPanelProps {
  sessionId: string;
  /** Proof that this browser owns the displayed visit; required by server-managed stations. */
  patientToken?: string;
  scenario?: Schema<"VitalsScenario"> | null;
  autoStart?: boolean;
  onReading?: () => void;
  /** Runs once, after the guided sequence and a fresh simulator reading have both completed. */
  onDone?: () => void;
}

function Tile({ label, value, unit, ready, children }: { label: string; value: string; unit: string; ready: boolean; children?: ReactNode }) {
  return (
    <section aria-label={label} className="vital-tile flex flex-col gap-1" data-ready={ready}>
      <h3 className="text-label font-semibold text-ink-muted">{label}</h3>
      <p className="text-display font-bold" dir="ltr">
        <span data-testid="vital-value">{ready ? value : "–"}</span> <span className="text-title font-semibold">{unit}</span>
      </p>
      {children}
    </section>
  );
}

/** A guided demo of the existing simulator. The browser never generates clinical readings. */
export function VitalsPanel({ sessionId, patientToken, scenario, autoStart = false, onReading, onDone }: VitalsPanelProps) {
  const t = useTranslations("vitals");
  const [vitals, setVitals] = useState<Vitals | null>(null);
  const [status, setStatus] = useState<State>("idle");
  const [elapsed, setElapsed] = useState(0);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const progressTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const deadlineTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const requestController = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const callbacks = useRef({ onReading, onDone });
  const notified = useRef(false);

  useEffect(() => { callbacks.current = { onReading, onDone }; }, [onReading, onDone]);

  const stopTimers = useCallback(() => {
    if (pollTimer.current) clearTimeout(pollTimer.current);
    if (progressTimer.current) clearInterval(progressTimer.current);
    if (deadlineTimer.current) clearTimeout(deadlineTimer.current);
    pollTimer.current = null;
    progressTimer.current = null;
    deadlineTimer.current = null;
    requestController.current?.abort();
    requestController.current = null;
  }, []);

  const start = useCallback(async () => {
    stopTimers();
    const attempt = ++generation.current;
    const current = () => generation.current === attempt;
    const fail = () => { if (current()) { ++generation.current; stopTimers(); setStatus("failed"); } };
    const controller = new AbortController();
    requestController.current = controller;
    deadlineTimer.current = setTimeout(fail, 35_000);
    const request = {
      signal: controller.signal,
      headers: patientToken ? { "X-CareFlow-Patient": patientToken } : undefined,
      params: { path: { session_id: sessionId } },
    };
    notified.current = false;
    setVitals(null);
    setElapsed(0);
    setStatus("measuring");

    // Compare against a server timestamp, not the phone's clock. An earlier measurement must
    // never complete a new attempt while its simulator is still starting.
    const before = await api.GET("/v1/sessions/{session_id}/status", request).catch(() => null);
    if (!current()) return;
    if (!before?.data) { fail(); return; }
    const previous = before.data.vitals?.measured_at;
    const result = await api.POST("/v1/dev/sessions/{session_id}/vitals-sim", {
      ...request,
      body: scenario ? { scenario } : {},
    }).catch(() => null);
    if (!current()) return;
    if (!result?.data || result.error) { fail(); return; }

    const began = Date.now();
    progressTimer.current = setInterval(() => {
      if (current()) setElapsed(Math.min(Date.now() - began, MEASUREMENT_MS));
    }, 200);
    let polls = 0;
    const poll = async () => {
      if (!current()) return;
      if (++polls > POLL_LIMIT) { fail(); return; }
      const response = await api.GET("/v1/sessions/{session_id}/status", request).catch(() => null);
      if (!current()) return;
      if (!response?.data) { fail(); return; }
      const next = response.data.vitals;
      const timestamp = next ? Date.parse(next.measured_at) : NaN;
      const fresh = Number.isFinite(timestamp) && (!previous || timestamp > Date.parse(previous));
      if (next?.simulated === true && fresh) {
        setVitals(next);
        callbacks.current.onReading?.();
      }
      pollTimer.current = setTimeout(() => void poll(), POLL_MS);
    };
    void poll();
  }, [patientToken, scenario, sessionId, stopTimers]);

  const completeReading = vitals !== null && [vitals.heart_rate, vitals.spo2, vitals.temperature_c].every((value) => typeof value === "number" && Number.isFinite(value));
  useEffect(() => {
    if (status !== "measuring" || elapsed < MEASUREMENT_MS || !completeReading || notified.current) return;
    notified.current = true;
    ++generation.current;
    stopTimers();
    // Defer the transition out of this effect, and invalidate any in-flight poll on cleanup.
    const finish = setTimeout(() => {
      setStatus("complete");
      callbacks.current.onDone?.();
    }, 0);
    return () => clearTimeout(finish);
  }, [completeReading, elapsed, status, stopTimers]);

  useEffect(() => {
    if (!autoStart) return;
    const kickoff = setTimeout(() => void start(), 0);
    return () => clearTimeout(kickoff);
  }, [autoStart, start]);

  useEffect(() => () => { ++generation.current; stopTimers(); }, [stopTimers]);

  const show = (value: number | null | undefined, digits = 0) => (typeof value === "number" ? value.toFixed(digits) : "–");
  const hasBp = typeof vitals?.systolic_bp === "number" && typeof vitals?.diastolic_bp === "number";
  const stage = elapsed < 2_000 ? "position" : elapsed < 4_000 ? "pulse" : elapsed < 6_000 ? "oxygen" : elapsed < 8_000 ? "pressure" : elapsed < MEASUREMENT_MS ? "temperature" : "saving";
  const percent = status === "complete" ? 100 : Math.min(95, Math.round(elapsed / MEASUREMENT_MS * 100));
  const seconds = Math.max(0, Math.ceil((MEASUREMENT_MS - elapsed) / 1_000));
  const ready = (after: number) => vitals !== null && elapsed >= after;

  return (
    <section className="guided-vitals flex flex-col gap-5" data-state={status} aria-label={t("title")}>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h2 className="text-title font-bold">{t("title")}</h2>
        <span className="vitals-demo-badge rounded-control bg-secondary px-3 py-1 text-label font-semibold text-on-secondary">{t("guide.badge")}</span>
      </div>
      <div className="vitals-guidance">
        <div className="vitals-sensor" aria-hidden="true">
          <svg viewBox="0 0 120 120" fill="none"><rect x="21" y="12" width="78" height="96" rx="30" stroke="currentColor" strokeWidth="2"/><path d="M42 67V48a18 18 0 0 1 36 0v19M50 71V48a10 10 0 0 1 20 0v24M60 48v31M34 62c0 26 10 34 26 34s26-8 26-34" stroke="currentColor" strokeWidth="3" strokeLinecap="round"/><path d="M27 83h66" className="vitals-scan-line" stroke="currentColor" strokeWidth="2"/></svg>
        </div>
        <div>
          <h3 className="text-title font-semibold">{t(status === "complete" ? "guide.complete" : "guide.placeFinger")}</h3>
          <p className="vitals-demo-note">{t("guide.instructions")}</p>
          <p className="vitals-demo-note text-label text-ink-muted">{t("guide.touchIdNote")}</p>
        </div>
      </div>
      {status === "idle" || status === "failed" ? (
        <BigButton onClick={() => void start()}>{t(status === "failed" ? "guide.retry" : "guide.start")}</BigButton>
      ) : null}
      {status === "failed" ? <p role="alert" className="font-semibold text-danger">{t("guide.failed")}</p> : null}
      {status === "measuring" || status === "complete" ? (
        <div className="vitals-progress-panel">
          <div className="flex items-center justify-between gap-3">
            <p className="vitals-stage font-semibold" role="status">{t(status === "complete" ? "guide.complete" : `guide.stages.${stage}`)}</p>
            <span className="vitals-countdown" aria-hidden="true">{seconds > 0 ? t("guide.seconds", { seconds }) : t("guide.finishing")}</span>
          </div>
          <div className="vitals-progress" role="progressbar" aria-label={t("guide.progress")} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
            <span style={{ width: `${percent}%` }} />
          </div>
          <p className="text-label text-ink-muted">{t("guide.automatic")}</p>
        </div>
      ) : null}
      <div className="vitals-readings grid gap-4 sm:grid-cols-2">
        <Tile label={t("heartRate")} value={show(vitals?.heart_rate)} unit={t("perMinute")} ready={ready(2_000)} />
        <Tile label={t("spo2")} value={show(vitals?.spo2)} unit={t("percent")} ready={ready(4_000)}>
          <p className="text-label text-ink-muted">{t("skinTone")}</p>
        </Tile>
        <Tile label={t("bloodPressure")} value={hasBp ? `${show(vitals?.systolic_bp)}/${show(vitals?.diastolic_bp)}` : "–"} unit={t("mmHg")} ready={ready(6_000)}>
          {ready(6_000) && !hasBp ? <p className="text-label text-ink-muted">{t("guide.noCuff")}</p> : null}
        </Tile>
        <Tile label={t("temperature")} value={show(vitals?.temperature_c, 1)} unit={t("celsius")} ready={ready(8_000)} />
      </div>
    </section>
  );
}
