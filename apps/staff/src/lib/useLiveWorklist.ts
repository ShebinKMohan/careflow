"use client";

import type { Schema } from "@careflow/contracts";
import { useCallback, useEffect, useRef, useState } from "react";

import { API_BASE_URL, api } from "./api";
import { type LiveFrame, type LiveStatus, StaffLiveChannel, wsBaseUrl } from "./liveEvents";
import { browserLiveDeps } from "./staffChannel";
import { sortWorklist, type WorklistItem } from "./sortWorklist";
import type { ApiState } from "./useApiData";

/** While the live channel is down, the worklist is re-read this often. */
export const POLL_MS = 5_000;
const REFETCH_DEBOUNCE_MS = 150;
const REFETCH_EVENTS = /^(alert|triage|token|intake|patient|nurse)\./; // patient.feels_worse (M2-07), nurse.flag (M2-09)

export type AlertRaised = Schema<"AlertRaisedData">;

/**
 * The nurse worklist: `GET /v1/nurse/worklist`, sorted danger first, kept fresh by the live
 * channel (`alert.*`, `triage.*`, `token.*`, `intake.*`, `patient.*` and `nurse.*` refetch it) and
 * by polling every 5 s while the channel is paused. `onAlert` gets each `alert.raised` as it
 * arrives.
 */
export function useLiveWorklist(onAlert: (raised: AlertRaised) => void): {
  state: ApiState<WorklistItem[]>;
  live: LiveStatus;
  refresh: () => Promise<void>;
} {
  const [state, setState] = useState<ApiState<WorklistItem[]>>({ status: "loading" });
  const [live, setLive] = useState<LiveStatus>("connecting");
  const onAlertRef = useRef(onAlert);
  const refetchTimer = useRef<number | null>(null);

  useEffect(() => {
    onAlertRef.current = onAlert;
  }, [onAlert]);

  const refresh = useCallback(
    () =>
      fetchWorklist().then((next) =>
        // A failed refresh keeps the last good list on screen.
        setState((current) => (next.status === "failed" && current.status === "ready" ? current : next)),
      ),
    [],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const ready = state.status === "ready";
  useEffect(() => {
    if (!ready) return undefined;
    const scheduleRefetch = () => {
      if (refetchTimer.current !== null) window.clearTimeout(refetchTimer.current);
      refetchTimer.current = window.setTimeout(() => void refresh(), REFETCH_DEBOUNCE_MS);
    };
    const channel = new StaffLiveChannel(
      wsBaseUrl(API_BASE_URL),
      browserLiveDeps(),
      {
        onFrame: (frame: LiveFrame) => {
          if (frame.type === "alert.raised" && isAlertRaised(frame.data)) onAlertRef.current(frame.data);
          if (REFETCH_EVENTS.test(frame.type)) scheduleRefetch();
        },
        onStatus: (status) => {
          setLive(status);
          if (status === "live") scheduleRefetch(); // catch up after a gap
        },
      },
    );
    channel.start();
    return () => {
      channel.stop();
      if (refetchTimer.current !== null) window.clearTimeout(refetchTimer.current);
    };
  }, [ready, refresh]);

  useEffect(() => {
    if (live !== "paused") return undefined;
    const poll = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(poll);
  }, [live, refresh]);

  return { state, live, refresh };
}

async function fetchWorklist(): Promise<ApiState<WorklistItem[]>> {
  try {
    const { data, response } = await api.GET("/v1/nurse/worklist");
    if (data) return { status: "ready", data: sortWorklist(data) };
    return { status: response.status === 401 ? "unauthorized" : "failed" };
  } catch {
    return { status: "failed" };
  }
}

function isAlertRaised(data: unknown): data is AlertRaised {
  if (typeof data !== "object" || data === null) return false;
  const { session_id: sessionId, alert } = data as { session_id?: unknown; alert?: unknown };
  return typeof sessionId === "string" && typeof alert === "object" && alert !== null;
}
