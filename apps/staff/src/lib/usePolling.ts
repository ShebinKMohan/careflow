"use client";

import { useEffect, useState } from "react";

import type { ApiState } from "./useApiData";

/** Load now and every `intervalMs` (the public board); the last good data stays on screen while a
 * later poll fails. */
export function usePolling<T>(
  load: () => Promise<{ data?: T; response: Response }>,
  key: string,
  intervalMs: number,
): ApiState<T> {
  const [state, setState] = useState<ApiState<T>>({ status: "loading" });
  useEffect(() => {
    let active = true;
    const tick = () => {
      load()
        .then(({ data, response }) => {
          if (!active) return;
          if (data !== undefined) setState({ status: "ready", data });
          else if (response.status === 401) setState({ status: "unauthorized" });
          else setState((previous) => (previous.status === "ready" ? previous : { status: "failed" }));
        })
        .catch(() => {
          if (active) setState((previous) => (previous.status === "ready" ? previous : { status: "failed" }));
        });
    };
    tick();
    const timer = setInterval(tick, intervalMs);
    return () => {
      active = false;
      clearInterval(timer);
    };
    // `key` identifies the request; `load` is recreated on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, intervalMs]);
  return state;
}
