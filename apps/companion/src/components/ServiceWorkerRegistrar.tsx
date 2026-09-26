"use client";

import { useEffect } from "react";

/**
 * Registers public/sw.js in production builds only (in `next dev` it would cache hot-reloaded
 * chunks). The worker caches the static app shell and never an API response.
 */
export function ServiceWorkerRegistrar() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production" || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => undefined);
  }, []);
  return null;
}
