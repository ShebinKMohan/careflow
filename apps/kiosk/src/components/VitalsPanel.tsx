"use client";

import type { Schema } from "@careflow/contracts";
import { BigButton } from "@careflow/ui";
import { useTranslations } from "next-intl";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";

type Vitals = Schema<"LatestVitals">;

export const POLL_MS = 1_000;
const POLL_LIMIT = 90; // stop after about 90 s (the scenarios last 30 s)

export interface VitalsPanelProps {
  sessionId: string;
  /** From `?scenario=` (dev and E2E); the API falls back to VITALS_SCENARIO, then normal. */
  scenario?: Schema<"VitalsScenario"> | null;
  /** Start measuring on mount (used when a scenario is given in the URL). */
  autoStart?: boolean;
}

function Tile({ label, value, unit, children }: { label: string; value: string; unit: string; children?: ReactNode }) {
  return (
    <section aria-label={label} className="flex flex-col gap-1 rounded-card border-2 border-border bg-surface p-gutter">
      <h3 className="text-label font-semibold text-ink-muted">{label}</h3>
      <p className="text-display font-bold" dir="ltr">
        <span data-testid="vital-value">{value}</span> <span className="text-title font-semibold">{unit}</span>
      </p>
      {children}
    </section>
  );
}

/**
 * Live vitals from the (simulated) device: pulse, SpO2, temperature and, when measured, blood
 * pressure. "Measure vitals" starts the dev simulator; readings come from the session status,
 * polled every second until live updates arrive (M1-11). Every reading is labelled simulated.
 */
export function VitalsPanel({ sessionId, scenario, autoStart = false }: VitalsPanelProps) {
  const t = useTranslations("vitals");
  const [vitals, setVitals] = useState<Vitals | null>(null);
  const [status, setStatus] = useState<"idle" | "measuring" | "failed">("idle");
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const stop = useCallback(() => {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
  }, []);

  const start = useCallback(async () => {
    stop();
    setStatus("measuring");
    const { error } = await api.POST("/v1/dev/sessions/{session_id}/vitals-sim", {
      params: { path: { session_id: sessionId } },
      body: scenario ? { scenario } : {},
    });
    if (error) {
      setStatus("failed");
      return;
    }
    let polls = 0;
    const poll = async () => {
      polls += 1;
      if (polls > POLL_LIMIT) stop();
      const { data } = await api.GET("/v1/sessions/{session_id}/status", {
        params: { path: { session_id: sessionId } },
      });
      if (data?.vitals) setVitals(data.vitals);
    };
    void poll();
    timer.current = setInterval(() => void poll(), POLL_MS);
  }, [scenario, sessionId, stop]);

  useEffect(() => {
    if (!autoStart) return undefined;
    const kickoff = setTimeout(() => void start(), 0);
    return () => {
      clearTimeout(kickoff);
      stop();
    };
  }, [autoStart, start, stop]);

  useEffect(() => stop, [stop]);

  const show = (value: number | undefined, digits = 0) => (value === undefined ? "–" : value.toFixed(digits));
  const hasBp = vitals?.systolic_bp !== undefined && vitals?.diastolic_bp !== undefined;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h2 className="text-title font-bold">{t("title")}</h2>
        <span className="rounded-control bg-secondary px-3 py-1 text-label font-semibold text-on-secondary">{t("simulated")}</span>
      </div>
      <BigButton onClick={() => void start()} disabled={status === "measuring" && vitals === null}>
        {status === "measuring" && vitals === null ? t("measuring") : t("measure")}
      </BigButton>
      {status === "failed" ? (
        <p role="alert" className="font-semibold text-danger">
          {t("failed")}
        </p>
      ) : null}
      {status === "measuring" && vitals === null ? <p aria-live="polite">{t("waiting")}</p> : null}
      <div className="grid gap-4 sm:grid-cols-2" aria-live="polite">
        <Tile label={t("heartRate")} value={show(vitals?.heart_rate)} unit={t("perMinute")} />
        <Tile label={t("spo2")} value={show(vitals?.spo2)} unit={t("percent")}>
          <p className="text-label text-ink-muted">{t("skinTone")}</p>
        </Tile>
        <Tile label={t("temperature")} value={show(vitals?.temperature_c, 1)} unit={t("celsius")} />
        {hasBp ? (
          <Tile
            label={t("bloodPressure")}
            value={`${show(vitals?.systolic_bp)}/${show(vitals?.diastolic_bp)}`}
            unit={t("mmHg")}
          />
        ) : null}
      </div>
    </div>
  );
}
