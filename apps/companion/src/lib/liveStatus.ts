"use client";

import type { Schema } from "@careflow/contracts";
import { useCallback, useEffect, useRef, useState } from "react";

import { API_BASE_URL, api } from "./api";
import { readSession } from "./session";

export type CompanionStatus = Schema<"CompanionStatus">;
export type LiveStatus =
  | { status: "loading" }
  | { status: "ready"; data: CompanionStatus; updatedAt: number; live: boolean }
  | { status: "unauthorized" }
  | { status: "failed" };

export const POLL_MS = 15_000;
export const RECONNECT_MIN_MS = 1_000;
export const RECONNECT_MAX_MS = 30_000;

function wsBase(): string {
  return API_BASE_URL.replace(/^http/, "ws").replace(/\/+$/, "");
}

/**
 * The waiting room's live status (M2-07): `GET /v1/companion/status`, then the session channel
 * (`queue.status` frames; a one-time ticket from `POST /v1/ws-ticket`, never the token in a URL).
 * A dropped socket reconnects with backoff and a fresh ticket; until then the status is polled
 * every 15 s.
 */
export function useLiveStatus(): LiveStatus {
  const [state, setState] = useState<LiveStatus>({ status: "loading" });
  const patientType = useRef<CompanionStatus["patient_type"]>(undefined);

  const refresh = useCallback(async () => {
    try {
      const { data, response } = await api.GET("/v1/companion/status");
      if (data) {
        patientType.current = data.patient_type ?? patientType.current;
        setState((current) => ({
          status: "ready",
          data,
          updatedAt: Date.now(),
          live: current.status === "ready" ? current.live : false,
        }));
      } else setState({ status: response.status === 401 || response.status === 403 ? "unauthorized" : "failed" });
    } catch {
      setState((current) => (current.status === "ready" ? current : { status: "failed" }));
    }
  }, []);

  useEffect(() => {
    let stopped = false;
    let socket: WebSocket | null = null;
    let attempts = 0;
    let retry: ReturnType<typeof setTimeout> | null = null;
    const setLive = (live: boolean) =>
      setState((current) => (current.status === "ready" ? { ...current, live } : current));
    const poll = setInterval(() => {
      if (!socket || socket.readyState !== WebSocket.OPEN) void refresh();
    }, POLL_MS);

    async function connect() {
      retry = null;
      const sessionId = readSession()?.sessionId;
      if (!sessionId) return;
      const { data } = await api
        .POST("/v1/ws-ticket", { body: { channel: "session", session_id: sessionId } })
        .catch(() => ({ data: undefined }));
      if (stopped) return;
      if (!data) {
        schedule();
        return;
      }
      const query = new URLSearchParams({ ticket: data.ticket });
      socket = new WebSocket(`${wsBase()}/v1/ws/sessions/${encodeURIComponent(sessionId)}?${query.toString()}`);
      socket.onopen = () => {
        attempts = 0;
        setLive(true);
      };
      socket.onmessage = (event: MessageEvent<unknown>) => {
        let frame: { type?: string; data?: CompanionStatus } = {};
        try {
          frame = JSON.parse(String(event.data)) as typeof frame;
        } catch {
          return;
        }
        if (frame.type === "queue.status" && frame.data) {
          const next = { ...frame.data, patient_type: frame.data.patient_type ?? patientType.current };
          setState({ status: "ready", data: next, updatedAt: Date.now(), live: true });
        } else if (frame.type === "token.called" || frame.type === "token.no_show") {
          void refresh();
        }
      };
      socket.onclose = () => {
        socket = null;
        setLive(false);
        if (!stopped) schedule();
      };
    }

    function schedule() {
      const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_MIN_MS * 2 ** attempts);
      attempts += 1;
      retry = setTimeout(() => void connect(), delay);
    }

    const start = setTimeout(() => {
      void refresh();
      void connect();
    }, 0);
    return () => {
      stopped = true;
      clearTimeout(start);
      clearInterval(poll);
      if (retry) clearTimeout(retry);
      if (socket) {
        socket.onclose = null;
        socket.close(1000);
      }
    };
  }, [refresh]);

  return state;
}
