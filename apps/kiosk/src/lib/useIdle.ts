"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** No input for this long shows "Are you still there?". */
export const IDLE_MS = 60_000;
/** Then this many seconds until the session is wiped. */
export const COUNTDOWN_SECONDS = 15;
const EVENTS = ["pointerdown", "keydown", "touchstart", "input", "wheel"] as const;

export interface Idle {
  warning: boolean;
  secondsLeft: number;
  /** The patient answered the prompt: start the idle clock again. */
  stillHere: () => void;
}

/**
 * Watches for input on the whole page. It works from `Date.now()` on a 1 s tick, so a fake
 * clock that jumps ahead (Playwright's `page.clock`) times out correctly.
 */
export function useIdle(enabled: boolean, onTimeout: () => void): Idle {
  const last = useRef(0);
  const [idleMs, setIdleMs] = useState(0);
  const timeout = useRef(onTimeout);

  useEffect(() => {
    timeout.current = onTimeout;
  }, [onTimeout]);

  const stillHere = useCallback(() => {
    last.current = Date.now();
    setIdleMs(0);
  }, []);

  useEffect(() => {
    if (!enabled) return undefined;
    last.current = Date.now();
    const touch = () => {
      last.current = Date.now();
    };
    for (const name of EVENTS) window.addEventListener(name, touch, { passive: true });
    const tick = setInterval(() => {
      const idle = Date.now() - last.current;
      if (idle >= IDLE_MS + COUNTDOWN_SECONDS * 1000) {
        last.current = Date.now();
        setIdleMs(0);
        timeout.current();
        return;
      }
      setIdleMs(idle);
    }, 1_000);
    return () => {
      clearInterval(tick);
      for (const name of EVENTS) window.removeEventListener(name, touch);
      setIdleMs(0);
    };
  }, [enabled]);

  const warning = enabled && idleMs >= IDLE_MS;
  const secondsLeft = Math.max(0, COUNTDOWN_SECONDS - Math.floor((idleMs - IDLE_MS) / 1_000));
  return { warning, secondsLeft, stillHere };
}
