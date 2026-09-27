"use client";

import { usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useCallback } from "react";

import { useKiosk } from "@/lib/session";
import { useIdle } from "@/lib/useIdle";

/** Screens that belong to one patient; the welcome and staff setup screens do not time out. */
const PATIENT_ROUTES = ["/checkin", "/patient", "/intake", "/vitals", "/done"];

/** After 60 s without input: "Are you still there?" with a 15 s countdown, then wipe and restart. */
export function IdleGuard() {
  const t = useTranslations("idle");
  const router = useRouter();
  const pathname = usePathname();
  const { wipe } = useKiosk();
  const onTimeout = useCallback(() => {
    wipe();
    router.push("/");
  }, [wipe, router]);
  const { warning, secondsLeft, stillHere } = useIdle(PATIENT_ROUTES.includes(pathname), onTimeout);

  if (!warning) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/70 p-gutter">
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="idle-title"
        className="flex w-full max-w-xl flex-col gap-4 rounded-card bg-surface p-gutter text-ink"
      >
        <h2 id="idle-title" className="text-title font-bold">
          {t("title")}
        </h2>
        <p className="text-body" aria-live="assertive">
          {t("countdown", { seconds: secondsLeft })}
        </p>
        <button
          type="button"
          className="min-h-touch rounded-control bg-primary px-6 text-title font-semibold text-on-primary"
          onClick={stillHere}
        >
          {t("stay")}
        </button>
      </div>
    </div>
  );
}
