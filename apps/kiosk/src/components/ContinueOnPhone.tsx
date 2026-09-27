"use client";

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { QRCodeSVG } from "qrcode.react";
import { useCallback, useEffect, useRef, useState } from "react";

import { patientApi } from "@/lib/api";
import { useKiosk } from "@/lib/session";
import { watchSession } from "@/lib/sessionChannel";

/** Where the phone opens the hand-over (the companion PWA). */
export const COMPANION_URL = (process.env.NEXT_PUBLIC_COMPANION_URL ?? "http://localhost:3002").replace(/\/+$/, "");
/** How long the kiosk shows "Continued on your phone" before it forgets the patient. */
export const HANDED_OVER_MS = 5_000;

const BUTTON = "min-h-touch rounded-control border-2 border-primary px-6 text-title font-semibold text-primary";
// After "ask this on your phone" (M2-10): the same button, filled, so it is easy to find.
const HIGHLIGHTED = "min-h-touch rounded-control border-4 border-primary bg-primary px-6 text-title font-semibold text-on-primary";

/**
 * "Continue on your phone" (M2-06): a one-time QR code for this session, 120 seconds, renewed when
 * it runs out. The code is `…/claim#t=<token>`: in the URL fragment, so the phone never sends it in
 * a request. When the phone claims it, the session's channel sends `session.handed_over`; the kiosk
 * says "Continued on your phone" for 5 s, then forgets the patient and returns to the welcome
 * screen. `highlight` fills the button after the patient asked a question the phone can answer
 * (M2-10).
 */
export function ContinueOnPhone({ highlight = false }: { highlight?: boolean }) {
  const t = useTranslations("intake.phone");
  const router = useRouter();
  const { patient, wipe } = useKiosk();
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState<{ token: string; expiresAt: number } | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [failed, setFailed] = useState(false);
  const [handedOver, setHandedOver] = useState(false);
  const token = patient?.token;
  const sessionId = patient?.sessionId;
  const expiresAt = useRef<number | null>(null);
  const fetching = useRef(false);

  const fetchCode = useCallback(async () => {
    if (!token || !sessionId || fetching.current) return;
    fetching.current = true;
    const { data } = await patientApi(token).POST("/v1/sessions/{session_id}/qr", {
      params: { path: { session_id: sessionId } },
    });
    fetching.current = false;
    if (!data) {
      expiresAt.current = null;
      setFailed(true);
      setCode(null);
      return;
    }
    expiresAt.current = Date.parse(data.expires_at);
    setFailed(false);
    setCode({ token: data.qr_token, expiresAt: expiresAt.current });
  }, [token, sessionId]);

  // While the code is shown: a 1-second countdown and a new code when it runs out.
  useEffect(() => {
    if (!open) return undefined;
    const timer = setInterval(() => {
      const current = Date.now();
      setNow(current);
      if (expiresAt.current !== null && current >= expiresAt.current) void fetchCode();
    }, 1_000);
    return () => clearInterval(timer);
  }, [open, fetchCode]);
  useEffect(() => {
    if (!open || !token || !sessionId) return undefined;
    let close: (() => void) | null = null;
    let active = true;
    void watchSession(token, sessionId, (type) => {
      if (type === "session.handed_over") setHandedOver(true);
    }).then((stop) => {
      if (active) close = stop;
      else stop();
    });
    return () => {
      active = false;
      close?.();
    };
  }, [open, token, sessionId]);
  useEffect(() => {
    if (!handedOver) return undefined;
    const reset = setTimeout(() => {
      wipe();
      router.push("/");
    }, HANDED_OVER_MS);
    return () => clearTimeout(reset);
  }, [handedOver, wipe, router]);

  if (!patient) return null;
  if (handedOver) {
    return (
      <p role="status" className="text-title font-semibold" data-testid="handed-over">
        {t("handedOver")}
      </p>
    );
  }
  if (!open) {
    return (
      <button
        type="button"
        className={highlight ? HIGHLIGHTED : BUTTON}
        onClick={() => {
          setOpen(true);
          void fetchCode();
        }}
        data-testid="continue-on-phone"
        data-highlighted={highlight ? "true" : undefined}
      >
        {t("button")}
      </button>
    );
  }
  const seconds = code ? Math.max(0, Math.ceil((code.expiresAt - now) / 1000)) : null;
  return (
    <section aria-label={t("title")} className="flex flex-col items-start gap-3 rounded-card border-2 border-border p-4">
      <h2 className="text-title font-semibold">{t("title")}</h2>
      {failed ? (
        <p role="alert" className="font-semibold">
          {t("failed")}
        </p>
      ) : code ? (
        <>
          <div className="rounded-control bg-white p-3" data-testid="handover-qr">
            <QRCodeSVG value={`${COMPANION_URL}/claim#t=${code.token}`} size={220} marginSize={2} />
          </div>
          <p className="text-label text-ink-muted" aria-live="polite">
            {t("expires", { seconds: seconds ?? 0 })}
          </p>
        </>
      ) : null}
      <button type="button" className={BUTTON} onClick={() => setOpen(false)}>
        {t("stay")}
      </button>
    </section>
  );
}
