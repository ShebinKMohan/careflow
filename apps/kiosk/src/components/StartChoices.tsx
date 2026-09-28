"use client";

import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { QRCodeSVG } from "qrcode.react";
import { useEffect, useState, useSyncExternalStore } from "react";

import { api } from "@/lib/api";
import { readDevice, subscribeDevice } from "@/lib/session";

import { COMPANION_URL } from "./ContinueOnPhone";
import { KioskIcon } from "./KioskIcon";

/** A reusable, expiring clinic entry ticket, before any patient details are collected. */
export function StartChoices({ preview = false }: { preview?: boolean }) {
  const t = useTranslations("experience");
  const locale = useLocale() === "ar" ? "ar" : "en";
  const router = useRouter();
  const [code, setCode] = useState<{ token: string; language: string } | null>(null);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  const hasDevice = useSyncExternalStore(subscribeDevice, () => readDevice() !== null, () => false);

  useEffect(() => {
    if (preview) return;
    let stopped = false;
    let requesting = false;
    let refreshAt = 0;
    let activeRequest: AbortController | null = null;
    const beganAt = Date.now();
    const refresh = async () => {
      if (requesting || stopped) return;
      if (!readDevice()) {
        if (Date.now() - beganAt > 12_000) setFailed(true);
        return;
      }
      if (Date.now() < refreshAt) return;
      requesting = true;
      activeRequest = new AbortController();
      const timeout = setTimeout(() => activeRequest?.abort(), 10_000);
      const result = await api.POST("/v1/checkin/start/qr", { body: { language: locale }, signal: activeRequest.signal }).catch(() => null);
      clearTimeout(timeout);
      requesting = false;
      if (stopped) return;
      refreshAt = result?.data ? Date.parse(result.data.expires_at) - 45_000 : Date.now() + 30_000;
      if (result?.data) { setCode({ token: result.data.qr_token, language: locale }); setFailed(false); }
      else { setCode(null); setFailed(true); }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 500);
    return () => { stopped = true; clearInterval(timer); activeRequest?.abort(); };
  }, [locale, retry, hasDevice, preview]);

  return (
    <div className="entry-options">
      <section className="entry-option entry-kiosk">
        <span className="entry-option-icon"><KioskIcon name="card" /></span>
        <h2>{t("kioskTitle")}</h2>
        <p>{t("kioskDescription")}</p>
        <div className="entry-kiosk-art" aria-hidden="true"><span className="entry-kiosk-screen"><KioskIcon name="cross" /><i /><i /><span><b /><b /><b /></span></span><span className="entry-kiosk-stand" /></div>
        <button type="button" className="entry-kiosk-action" disabled={preview || !hasDevice} onClick={() => router.push("/checkin")}>{t("kioskAction")}<KioskIcon name="arrow" /></button>
        <span className="entry-option-note" role="status">{t(preview ? "cloudPreviewStatus" : hasDevice ? "languageHint" : failed ? "stationUnavailable" : "stationPreparing")}</span>
      </section>
      <section className="entry-option entry-phone">
        <span className="entry-option-icon"><KioskIcon name="phone" /></span>
        <h2>{t(preview ? "cloudPreviewPhoneTitle" : "phoneTitle")}</h2>
        <p>{t("phoneDescription")}</p>
        <div className="entry-qr-area" aria-live="polite">
          {preview ? <div className="entry-qr-message"><KioskIcon name="phone" /><p>{t("cloudPreviewPhoneStatus")}</p></div> : code && code.language === locale && !failed ? <div className="entry-qr" data-testid="entry-qr"><QRCodeSVG value={`${COMPANION_URL}/start?l=${locale}#t=${code.token}`} size={180} marginSize={2} /></div> : <div className="entry-qr-message"><KioskIcon name={failed ? "help" : "phone"} /><p>{t(failed ? hasDevice ? "qrUnavailable" : "stationUnavailable" : "qrLoading")}</p>{failed ? <button type="button" onClick={() => { window.dispatchEvent(new Event("careflow:retry-device")); setFailed(false); setRetry((value) => value + 1); }}>{t("qrRetry")}</button> : <span className="entry-loading" aria-hidden="true" />}</div>}
        </div>
        <span className="entry-option-note"><KioskIcon name="shield" />{t("phoneNote")}</span>
      </section>
    </div>
  );
}
