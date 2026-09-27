"use client";

import { useEffect, useState } from "react";

import type { ApiState } from "./useApiData";

export interface Polled<T> {
  state: ApiState<T>;
  /** When the data on screen was fetched (ms since the epoch); null before the first answer. */
  updatedAt: number | null;
  /** The last poll failed while older data is on screen: it may be out of date. */
  stale: boolean;
}

/** Load now and every `intervalMs` (the public board). A failed poll keeps the last good data on
 * screen but marks it stale, so the screen can say so; the next answer replaces it with a fresh
 * snapshot and clears the mark. */
export function usePolling<T>(
  load: () => Promise<{ data?: T; response: Response }>,
  key: string,
  intervalMs: number,
  now: () => number = Date.now,
): Polled<T> {
  const [polled, setPolled] = useState<Polled<T>>({ state: { status: "loading" }, updatedAt: null, stale: false });
  useEffect(() => {
    let active = true;
    const failed = () =>
      setPolled((previous) =>
        previous.state.status === "ready" ? { ...previous, stale: true } : { state: { status: "failed" }, updatedAt: null, stale: false },
      );
    const tick = () => {
      load()
        .then(({ data, response }) => {
          if (!active) return;
          if (data !== undefined) setPolled({ state: { status: "ready", data }, updatedAt: now(), stale: false });
          else if (response.status === 401) setPolled({ state: { status: "unauthorized" }, updatedAt: null, stale: false });
          else failed();
        })
        .catch(() => {
          if (active) failed();
        });
    };
    tick();
    const timer = setInterval(tick, intervalMs);
    return () => {
      active = false;
      clearInterval(timer);
    };
    // `key` identifies the request; `load` and `now` are recreated on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, intervalMs]);
  return polled;
}
