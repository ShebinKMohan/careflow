"use client";

import { useEffect, useState } from "react";

/** Date.now(), refreshed every `intervalMs` (Playwright's page.clock moves it in tests). */
export function useNow(intervalMs = 1_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}
