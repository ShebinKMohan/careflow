"use client";

import { useEffect, useRef, useState } from "react";

import { API_BASE_URL } from "./api";
import { type LiveStatus, StaffLiveChannel, wsBaseUrl } from "./liveEvents";
import { browserLiveDeps, frameSession } from "./staffChannel";

/** While the live channel is down, the session card is re-read this often. */
export const SESSION_POLL_MS = 5_000;
const RELOAD_DEBOUNCE_MS = 150;
/** The frames that change what the nurse's session card shows (M2-07). */
const RELOAD_EVENTS = /^(alert|triage|token|intake|patient|nurse)\./;

/**
 * Keeps one session's card fresh (M2-07): a staff-channel frame about this session reloads it (an
 * alert raised or "I feel worse" pressed after the decision, a task done elsewhere), so does every
 * (re)connection, which is the snapshot for anything missed while the socket was down, and while
 * the channel is paused the card is re-read every 5 s. Returns the channel's status.
 */
export function useLiveSession(sessionId: string, reload: () => void): LiveStatus {
  const [live, setLive] = useState<LiveStatus>("connecting");
  const reloadRef = useRef(reload);

  useEffect(() => {
    reloadRef.current = reload;
  }, [reload]);

  useEffect(() => {
    let timer: number | null = null;
    const schedule = () => {
      if (timer !== null) window.clearTimeout(timer);
      timer = window.setTimeout(() => reloadRef.current(), RELOAD_DEBOUNCE_MS);
    };
    const channel = new StaffLiveChannel(wsBaseUrl(API_BASE_URL), browserLiveDeps(), {
      onFrame: (frame) => {
        if (RELOAD_EVENTS.test(frame.type) && frameSession(frame) === sessionId) schedule();
      },
      onStatus: (status) => {
        setLive(status);
        if (status === "live") schedule(); // the snapshot after a gap
      },
    });
    channel.start();
    return () => {
      channel.stop();
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [sessionId]);

  useEffect(() => {
    if (live !== "paused") return undefined;
    const poll = window.setInterval(() => reloadRef.current(), SESSION_POLL_MS);
    return () => window.clearInterval(poll);
  }, [live]);

  return live;
}
