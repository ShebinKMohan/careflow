"use client";

import { useEffect } from "react";

import { RENEW_BEFORE_MS, renewToken, RETRY_MS, tokenExpiry } from "@/lib/renewal";
import { readToken, TOKEN_SAVED } from "@/lib/session";

/**
 * Keeps the phone's session token alive while the patient waits (M2-06): renews it 10 minutes
 * before it runs out, and at once when the phone wakes or comes back online close to that. A phone
 * asleep past the token's 30 minutes has to ask at the desk: an expired token never renews.
 */
export function TokenRenewer() {
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let active = true;
    const schedule = (delay?: number) => {
      clearTimeout(timer);
      const token = readToken();
      const expiry = token ? tokenExpiry(token) : null;
      if (expiry === null) return; // no visit on this phone: nothing to keep alive
      const due = delay ?? Math.max(0, expiry - RENEW_BEFORE_MS - Date.now());
      timer = setTimeout(() => {
        void renewToken().then((outcome) => {
          if (!active) return;
          if (outcome === "unavailable") schedule(RETRY_MS);
          // "renewed" stores the new token, whose event reschedules; "ended" leaves nothing to do.
        });
      }, due);
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") schedule();
    };
    const onChange = () => schedule();
    schedule();
    window.addEventListener(TOKEN_SAVED, onChange);
    window.addEventListener("online", onChange);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      active = false;
      clearTimeout(timer);
      window.removeEventListener(TOKEN_SAVED, onChange);
      window.removeEventListener("online", onChange);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);
  return null;
}
