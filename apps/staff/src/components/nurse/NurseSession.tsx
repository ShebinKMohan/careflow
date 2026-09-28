"use client";

import type { Schema } from "@careflow/contracts";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "@/lib/api";
import { indexFacts } from "@/lib/facts";
import type { LiveStatus } from "@/lib/liveEvents";
import { raisedAfter } from "@/lib/sortWorklist";
import type { ApiState } from "@/lib/useApiData";
import { useLiveSession } from "@/lib/useLiveSession";
import type { Task } from "@/lib/useSessionAlerts";

import { ApiStatus } from "../ApiStatus";
import { SessionReception } from "../ReceptionSummary";
import { AlertBanner } from "./AlertBanner";
import { FeelsWorseBanner } from "./FeelsWorseBanner";
import { ClinicalSnapshot } from "./ClinicalSnapshot";
import { News2Form } from "./News2Form";
import { TriageCard } from "./TriageCard";

type TriageView = Schema<"TriageView">;
type StructuredFacts = Schema<"StructuredFacts">;
type SessionWarning = Schema<"SessionWarning">;

interface Loaded {
  triage: TriageView;
  facts: StructuredFacts | null;
  factsStale?: boolean;
  nurseUserId: string | null;
}

/**
 * One session for the nurse: its alerts first (safety rule 1), also after the decision, then any
 * "I feel worse" presses, the triage card and the NEWS2 inputs. Everything comes from
 * `GET /v1/sessions/{id}/triage` and `/facts`, and stays live (M2-07): a staff-channel frame about
 * this session reloads the card, as does every reconnection; while the channel is paused it is
 * re-read every 5 s. A failed reload keeps the last card on screen.
 */
export function NurseSession({ sessionId }: { sessionId: string }) {
  const t = useTranslations("safety.alerts");
  const [state, setState] = useState<ApiState<Loaded>>({ status: "loading" });
  // Loads overlap (a live frame, a reconnection, the poll). An answer is shown as soon as it comes,
  // unless a load started after it has already been shown: then it is older than the screen and
  // would put back what just changed (SHE-325).
  const started = useRef(0);
  const shown = useRef(0);
  const onScreen = useRef(sessionId); // a load for another session is never shown

  useEffect(() => {
    onScreen.current = sessionId;
    const load = ++started.current;
    void loadSession(sessionId).then((next) => {
      if (onScreen.current !== sessionId || load <= shown.current) return;
      shown.current = load;
      setState(next);
    });
  }, [sessionId]);

  const reload = useCallback(() => {
    const load = ++started.current;
    void loadSession(sessionId).then((next) => {
      if (onScreen.current !== sessionId || load <= shown.current) return;
      shown.current = load;
      setState((current) => {
        if (current.status !== "ready") return next;
        if (next.status !== "ready") return { ...current, data: { ...current.data, factsStale: true } };
        if (!next.data.facts && current.data.facts) return { status: "ready", data: { ...next.data, facts: current.data.facts, factsStale: true } };
        return next;
      });
    });
  }, [sessionId]);
  const live = useLiveSession(sessionId, reload);

  const update = useCallback((change: (loaded: Loaded) => Loaded) => {
    setState((current) => (current.status === "ready" ? { status: "ready", data: change(current.data) } : current));
  }, []);

  return (
    <ApiStatus state={state.status === "ready" && state.data.triage.session_id !== sessionId ? { status: "loading" } : state}>
      {(loaded) => <SessionView loaded={loaded} update={update} sectionLabel={t("title")} live={live} />}
    </ApiStatus>
  );
}

async function loadSession(sessionId: string): Promise<ApiState<Loaded>> {
  const params = { params: { path: { session_id: sessionId } } };
  try {
    const [triage, facts, me] = await Promise.all([
      api.GET("/v1/sessions/{session_id}/triage", params),
      api.GET("/v1/sessions/{session_id}/facts", params).catch(() => ({ data: undefined })),
      api.GET("/v1/auth/me").catch(() => ({ data: undefined })),
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
  live,
}: {
  loaded: Loaded;
  update: (change: (loaded: Loaded) => Loaded) => void;
  sectionLabel: string;
  live: LiveStatus;
}) {
  const tNurse = useTranslations("nurse");
  const clinical = useTranslations("clinical");
  const { triage, facts } = loaded;
  const index = useMemo(() => indexFacts(facts), [facts]);
  const tasks = triage.tasks ?? triage.open_tasks ?? []; // done tasks too: "ECG done" survives a reload
  const warnings: SessionWarning[] = [...(triage.warnings ?? [])];
  if (facts?.extraction_status === "incomplete" && !warnings.includes("extraction_incomplete")) {
    warnings.unshift("extraction_incomplete");
  }
  const systolic = facts?.vitals?.systolic_bp;
  const deviceSystolic = systolic?.provenance === "DEVICE_MEASURED" ? systolic.value : undefined;
  const decidedAt = triage.status === "confirmed" ? triage.decision?.decided_at : undefined;

  const onTaskDone = (task: Task) =>
    update((current) => ({
      ...current,
      triage: {
        ...current.triage,
        open_tasks: (current.triage.open_tasks ?? []).map((existing) => (existing.task_id === task.task_id ? task : existing)),
        tasks: (current.triage.tasks ?? current.triage.open_tasks ?? []).map((existing) => (existing.task_id === task.task_id ? task : existing)),
      },
    }));

  return (
    <div className="staff-session">
      <p
        className={`w-fit rounded-control border-2 px-3 py-1 font-semibold ${live === "paused" ? "border-danger text-danger" : "border-border"}`}
        data-testid="live-status"
      >
        {live === "live" ? tNurse("live") : live === "paused" ? tNurse("paused") : tNurse("connecting")}
      </p>
      {triage.alerts.length ? (
        // Every alert of the session, before and after the decision: a patient getting worse while
        // waiting must never be hidden by an earlier decision (M2-07).
        <div className="sticky top-0 z-10 flex max-h-[45vh] flex-col gap-3 overflow-y-auto" role="region" aria-label={sectionLabel}>
          {triage.alerts.map((alert) => (
            <AlertBanner
              key={alert.alert_id}
              alert={alert}
              facts={index}
              tasks={tasks}
              onTaskDone={onTaskDone}
              afterDecision={raisedAfter(alert.raised_at, decidedAt)}
            />
          ))}
        </div>
      ) : null}
      <FeelsWorseBanner sessionId={triage.session_id} report={triage.feels_worse} />
      <SessionReception key={triage.session_id} sessionId={triage.session_id} />
      {loaded.factsStale ? <p className="staff-clinical-unavailable" role="status">{clinical("stale")}</p> : !facts ? <p className="staff-clinical-unavailable" role="status">{clinical("unavailable")}</p> : null}
      <div className="staff-assessment-grid">
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
      <ClinicalSnapshot facts={facts} neededFacts={triage.needed_facts} />
    </div>
  );
}
