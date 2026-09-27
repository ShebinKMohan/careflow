"use client";

import type { Schema } from "@careflow/contracts";
import { Card } from "@careflow/ui";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useMemo, useState } from "react";

import { patientApi } from "@/lib/api";
import { useKiosk } from "@/lib/session";

export const STATUS_POLL_MS = 2_000;
/** After the token is shown, the kiosk starts over for the next patient. */
export const RESET_AFTER_TOKEN_MS = 30_000;

/**
 * "Please take a seat": polls the session status every 2 s and shows the token and room once
 * assigned (in M1 the patient waits here for it; M2-06 moves it to their phone). It never shows
 * a level or an alert: the status route carries neither.
 */
export function DoneScreen() {
  const t = useTranslations("done");
  const tChild = useTranslations("safety.child");
  const router = useRouter();
  const { patient, wipe } = useKiosk();
  const [status, setStatus] = useState<Schema<"SessionStatus"> | null>(null);
  const token = patient?.token;
  const sessionId = patient?.sessionId;
  const client = useMemo(() => (token ? patientApi(token) : null), [token]);
  const tokenCode = status?.token_code;

  useEffect(() => {
    if (!client || !sessionId) return undefined;
    let stopped = false;
    const poll = async () => {
      const { data } = await client.GET("/v1/sessions/{session_id}/status", {
        params: { path: { session_id: sessionId } },
      });
      if (!stopped && data) setStatus(data);
    };
    void poll();
    const timer = setInterval(() => void poll(), STATUS_POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [client, sessionId]);

  useEffect(() => {
    if (!tokenCode) return undefined;
    const reset = setTimeout(() => {
      wipe();
      router.push("/");
    }, RESET_AFTER_TOKEN_MS);
    return () => clearTimeout(reset);
  }, [tokenCode, wipe, router]);

  if (!patient) {
    return (
      <Card>
        <p>{t("noSession")}</p>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-6" aria-live="polite">
      {patient.patientType === "child" ? (
        <div className="flex flex-col gap-2" data-testid="child-nurse">
          <p className="text-title font-semibold">{tChild("nurseTold")}</p>
          <p className="text-title">{tChild("nurseCheck")}</p>
        </div>
      ) : null}
      <p className="text-title">{t("seat")}</p>
      {tokenCode ? (
        <Card title={t("tokenLabel")}>
          <p className="text-display font-bold" dir="ltr" data-testid="token-code">
            {tokenCode}
          </p>
          {status?.room ? (
            <p className="text-title font-semibold" data-testid="room">
              {t.rich("room", { room: status.room, ltr: (chunks) => <bdi dir="ltr">{chunks}</bdi> })}
            </p>
          ) : (
            <p className="text-title">{t("waiting")}</p>
          )}
          {status?.wait_estimate_min !== undefined ? (
            <p className="text-label text-ink-muted">{t("estimate", { minutes: status.wait_estimate_min })}</p>
          ) : null}
        </Card>
      ) : null}
    </div>
  );
}
