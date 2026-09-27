"use client";

import type { Schema } from "@careflow/contracts";
import { useCallback, useEffect, useRef, useState } from "react";

import type { Alert } from "./alerts";
import { api } from "./api";
import { type FactView, indexFacts } from "./facts";
import { awaitingDecision, type WorklistItem } from "./sortWorklist";
import type { AlertRaised } from "./useLiveWorklist";

export type Task = Schema<"Task">;

export interface SessionAlerts {
  sessionId: string;
  patientDisplay: string;
  alerts: Alert[];
  facts: Map<string, FactView>;
  tasks: Task[];
}

/**
 * The full alerts (text, evidence, deadline) behind the worklist's alert chips: for each session
 * still awaiting the nurse, `GET /v1/sessions/{id}/triage` and its facts. A pushed `alert.raised`
 * shows at once and loads that session's facts for its evidence. A confirmed session drops out.
 */
export function useSessionAlerts(items: WorklistItem[] | null): {
  sessions: SessionAlerts[];
  push: (raised: AlertRaised) => void;
  updateTask: (task: Task) => void;
} {
  const [details, setDetails] = useState<Map<string, SessionAlerts>>(new Map());
  const requested = useRef(new Set<string>());

  const load = useCallback(async (sessionId: string, patientDisplay: string) => {
    const params = { params: { path: { session_id: sessionId } } };
    const [triage, facts] = await Promise.all([
      api.GET("/v1/sessions/{session_id}/triage", params).catch(() => ({ data: undefined })),
      api.GET("/v1/sessions/{session_id}/facts", params).catch(() => ({ data: undefined })),
    ]);
    setDetails((current) => {
      const next = new Map(current);
      const previous = next.get(sessionId);
      const alerts = mergeAlerts(triage.data?.alerts ?? [], previous?.alerts ?? []);
      next.set(sessionId, {
        sessionId,
        patientDisplay,
        alerts,
        facts: facts.data ? indexFacts(facts.data) : (previous?.facts ?? new Map()),
        tasks: triage.data?.open_tasks ?? previous?.tasks ?? [],
      });
      return next;
    });
  }, []);

  useEffect(() => {
    if (!items) return;
    for (const item of items) {
      if (!awaitingDecision(item) || item.alerts.length === 0) continue;
      if (requested.current.has(item.session_id)) continue;
      requested.current.add(item.session_id);
      void load(item.session_id, item.patient_display);
    }
  }, [items, load]);

  const push = useCallback(
    (raised: AlertRaised) => {
      const display = items?.find((item) => item.session_id === raised.session_id)?.patient_display ?? "";
      setDetails((current) => {
        const next = new Map(current);
        const previous = next.get(raised.session_id);
        next.set(raised.session_id, {
          sessionId: raised.session_id,
          patientDisplay: previous?.patientDisplay || display,
          alerts: mergeAlerts([raised.alert], previous?.alerts ?? []),
          facts: previous?.facts ?? new Map(),
          tasks: previous?.tasks ?? [],
        });
        return next;
      });
      requested.current.add(raised.session_id);
      void load(raised.session_id, display);
    },
    [items, load],
  );

  const updateTask = useCallback((task: Task) => {
    setDetails((current) => {
      const session = current.get(task.session_id);
      if (!session) return current;
      const next = new Map(current);
      next.set(task.session_id, {
        ...session,
        tasks: session.tasks.map((existing) => (existing.task_id === task.task_id ? task : existing)),
      });
      return next;
    });
  }, []);

  const confirmed = new Set((items ?? []).filter((item) => !awaitingDecision(item)).map((item) => item.session_id));
  const sessions = [...details.values()].filter((session) => !confirmed.has(session.sessionId) && session.alerts.length);
  return { sessions, push, updateTask };
}

function mergeAlerts(fresh: Alert[], known: Alert[]): Alert[] {
  const byId = new Map(known.map((alert) => [alert.alert_id, alert]));
  for (const alert of fresh) byId.set(alert.alert_id, alert);
  return [...byId.values()].sort(
    (a, b) => Number(b.severity === "critical") - Number(a.severity === "critical") || a.raised_at.localeCompare(b.raised_at),
  );
}
