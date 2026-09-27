"use client";

import { setLocaleCookie } from "@careflow/ui/i18n";
import { useRouter } from "next/navigation";
import { useLocale } from "next-intl";
import { useCallback, useEffect, useRef, useState } from "react";

import { claimVisit, newClaimKey, type PhoneVisit, resumeVisit } from "./phoneVisit";
import { clearSession } from "./session";

/** Share the first promise across Strict Mode's effect replay: a one-time QR code is claimed once. */
export function usePhoneVisit(fromLink = false) {
  const [visit, setVisit] = useState<PhoneVisit>({ status: "loading" });
  const operation = useRef<Promise<PhoneVisit> | null>(null);
  const retryAction = useRef<() => Promise<PhoneVisit>>(resumeVisit);
  const working = useRef(false);
  const router = useRouter();
  const locale = useLocale();

  const apply = useCallback((next: PhoneVisit) => {
    setVisit(next);
    if (next.status === "ready" && next.session.language !== locale) {
      setLocaleCookie(next.session.language);
      router.refresh();
    }
  }, [locale, router]);

  useEffect(() => {
    let active = true;
    if (!operation.current) {
      const code = fromLink ? new URLSearchParams(window.location.hash.slice(1)).get("t") : null;
      if (code) {
        window.history.replaceState(null, "", window.location.pathname + window.location.search);
        const key = newClaimKey(); // one per code: a retry proves it is this phone again
        retryAction.current = () => claimVisit(code, key, () => { retryAction.current = resumeVisit; });
      }
      operation.current = retryAction.current();
    }
    void operation.current.then((next) => { if (active) apply(next); });
    return () => { active = false; };
  }, [apply, fromLink]);

  async function run(action: () => Promise<PhoneVisit>) {
    if (working.current) return;
    working.current = true;
    retryAction.current = action;
    setVisit({ status: "loading" });
    try {
      operation.current = action();
      apply(await operation.current);
    } finally {
      working.current = false;
    }
  }

  return {
    visit,
    claim: (code: string) => {
      const key = newClaimKey();
      return run(() => claimVisit(code, key, () => { retryAction.current = resumeVisit; }));
    },
    retry: () => run(retryAction.current),
    reset: () => {
      clearSession();
      const empty: PhoneVisit = { status: "empty" };
      operation.current = Promise.resolve(empty);
      retryAction.current = resumeVisit;
      setVisit(empty);
    },
  };
}
