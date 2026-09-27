"use client";

import type { Schema } from "@careflow/contracts";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import { indexFacts } from "@/lib/facts";
import type { ApiState } from "@/lib/useApiData";
import type { Task } from "@/lib/useSessionAlerts";

import { ApiStatus } from "../ApiStatus";
import { AlertBanner } from "./AlertBanner";
import { News2Form } from "./News2Form";
import { TriageCard } from "./TriageCard";

type TriageView = Schema<"TriageView">;
type StructuredFacts = Schema<"StructuredFacts">;
type SessionWarning = Schema<"SessionWarning">;

interface Loaded {
  triage: TriageView;
  facts: StructuredFacts | null;
  nurseUserId: string | null;
}

/**
 * One session for the nurse: its alerts first (safety rule 1), then the triage card and the NEWS2
 * inputs. Everything comes from `GET /v1/sessions/{id}/triage` and `/facts`.
 */
export function NurseSession({ sessionId }: { sessionId: string }) {
  const t = useTranslations("alerts");
  const [state, setState] = useState<ApiState<Loaded>>({ status: "loading" });

  useEffect(() => {
    let active = true;
    void loadSession(sessionId).then((next) => {
      if (active) setState(next);
    });
    return () => {
      active = false;
    };
  }, [sessionId]);

  const update = useCallback((change: (loaded: Loaded) => Loaded) => {
    setState((current) => (current.status === "ready" ? { status: "ready", data: change(current.data) } : current));
  }, []);

  return (
    <ApiStatus state={state}>
      {(loaded) => <SessionView loaded={loaded} update={update} sectionLabel={t("title")} />}
    </ApiStatus>
  );
}

async function loadSession(sessionId: string): Promise<ApiState<Loaded>> {
  const params = { params: { path: { session_id: sessionId } } };
  try {
    const [triage, facts, me] = await Promise.all([
      api.GET("/v1/sessions/{session_id}/triage", params),
      api.GET("/v1/sessions/{session_id}/facts", params),
      api.GET("/v1/auth/me"),
    ]);
    if (!triage.data) return { status: triage.response.status === 401 ? "unauthorized" : "failed" };
    return {
      status: "ready",
      data: { triage: triage.data, facts: facts.data ?? null, nurseUserId: me.data?.user_id ?? null },
    };
  } catch {
    return { status: "failed" };
  }
}

function SessionView({
  loaded,
  update,
  sectionLabel,
}: {
  loaded: Loaded;
  update: (change: (loaded: Loaded) => Loaded) => void;
  sectionLabel: string;
}) {
  const { triage, facts } = loaded;
  const index = useMemo(() => indexFacts(facts), [facts]);
  const tasks = triage.open_tasks ?? [];
  const warnings: SessionWarning[] = [...(triage.warnings ?? [])];
  if (facts?.extraction_status === "incomplete" && !warnings.includes("extraction_incomplete")) {
    warnings.unshift("extraction_incomplete");
  }
  const systolic = facts?.vitals?.systolic_bp;
  const deviceSystolic = systolic?.provenance === "DEVICE_MEASURED" ? systolic.value : undefined;
  const awaiting = triage.status === "awaiting_nurse";

  const onTaskDone = (task: Task) =>
    update((current) => ({
      ...current,
      triage: {
        ...current.triage,
        open_tasks: (current.triage.open_tasks ?? []).map((existing) => (existing.task_id === task.task_id ? task : existing)),
      },
    }));

  return (
    <div className="flex max-w-4xl flex-col gap-6">
      {awaiting && triage.alerts.length ? (
        <div className="sticky top-0 z-10 flex flex-col gap-3" role="region" aria-label={sectionLabel}>
          {triage.alerts.map((alert) => (
            <AlertBanner key={alert.alert_id} alert={alert} facts={index} tasks={tasks} onTaskDone={onTaskDone} />
          ))}
        </div>
      ) : null}
      <TriageCard
        triage={triage}
        facts={index}
        warnings={warnings}
        nurseUserId={loaded.nurseUserId}
        onDecided={(result) =>
          update((current) => ({ ...current, triage: { ...current.triage, status: "confirmed", decision: result.decision } }))
        }
      />
      <News2Form
        sessionId={triage.session_id}
        news2={triage.news2}
        deviceSystolic={deviceSystolic}
        onSaved={(view) => update((current) => ({ ...current, triage: view }))}
      />
    </div>
  );
}
