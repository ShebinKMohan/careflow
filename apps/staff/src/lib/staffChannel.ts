"use client";

import { api } from "./api";
import type { LiveDeps, LiveFrame } from "./liveEvents";

/** The browser's side of the staff live channel: tickets from the API, real sockets and timers. */
export function browserLiveDeps(): LiveDeps {
  return {
    requestTicket: async () => {
      try {
        const { data } = await api.POST("/v1/ws-ticket", { body: { channel: "staff" } });
        return data?.ticket ?? null;
      } catch {
        return null;
      }
    },
    createSocket: (url) => new WebSocket(url),
    setTimeout: (callback, ms) => window.setTimeout(callback, ms),
    clearTimeout: (handle) => window.clearTimeout(handle as number),
  };
}

/** The session a frame is about (every session event's data carries `session_id`), if any. */
export function frameSession(frame: LiveFrame): string | undefined {
  const data = frame.data;
  if (data && typeof data === "object" && "session_id" in data) {
    const sessionId = (data as { session_id?: unknown }).session_id;
    return typeof sessionId === "string" ? sessionId : undefined;
  }
  return undefined;
}
