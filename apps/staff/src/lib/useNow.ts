"use client";

import { useEffect, useState } from "react";

/**
 * Date.now(), refreshed every `intervalMs` (Playwright's page.clock moves it in tests). It is read
 * again as soon as the component is on screen, not only on the first tick: a countdown shows the
 * time at mount even if the clock stood still between the first render and the effect.
 */
export function useNow(intervalMs = 1_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    // Read the clock at mount: the first render may be older than this effect (see above).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}
