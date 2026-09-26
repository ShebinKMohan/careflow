"use client";

import { useEffect, useState } from "react";

export type ApiState<T> =
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "unauthorized" }
  | { status: "failed" };

/** Load once on mount (client side, where the session token lives). */
export function useApiData<T>(load: () => Promise<{ data?: T; response: Response }>, key: string): ApiState<T> {
  const [state, setState] = useState<ApiState<T>>({ status: "loading" });
  useEffect(() => {
    let active = true;
    load()
      .then(({ data, response }) => {
        if (!active) return;
        if (data !== undefined) setState({ status: "ready", data });
        else setState({ status: response.status === 401 ? "unauthorized" : "failed" });
      })
      .catch(() => {
        if (active) setState({ status: "failed" });
      });
    return () => {
      active = false;
    };
    // `key` identifies the request; `load` is recreated on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return state;
}
