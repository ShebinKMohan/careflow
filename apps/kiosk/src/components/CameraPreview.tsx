"use client";

import { useTranslations } from "next-intl";
import { useCallback, useEffect, useRef, useState } from "react";
import { hasConsent } from "@/consent/hasConsent";
import { useKiosk } from "@/lib/session";
import { KioskIcon } from "./KioskIcon";

/** A local preview only. No frame capture, recording, upload, or inferred clinical findings. */
export function CameraPreview() {
  const t = useTranslations("cameraPreview");
  const { patient, consent, onWipe } = useKiosk();
  const [state, setState] = useState<"off" | "requesting" | "on" | "denied" | "unavailable">("off");
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const generation = useRef(0);
  const allowed = Boolean(patient && consent?.camera && hasConsent("camera"));
  const stop = useCallback(() => {
    generation.current += 1;
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
    if (video.current) video.current.srcObject = null;
  }, []);
  useEffect(() => {
    const hide = () => {
      if (document.visibilityState === "hidden") { stop(); setState("off"); }
    };
    const wipe = onWipe(stop);
    document.addEventListener("visibilitychange", hide);
    window.addEventListener("pagehide", stop);
    return () => { stop(); wipe(); document.removeEventListener("visibilitychange", hide); window.removeEventListener("pagehide", stop); };
  }, [stop, onWipe, allowed, patient?.sessionId]);

  async function enable() {
    if (!allowed || !hasConsent("camera")) return;
    stop();
    const request = generation.current;
    setState("requesting");
    if (!navigator.mediaDevices?.getUserMedia) { setState("unavailable"); return; }
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } } });
      if (request !== generation.current || !hasConsent("camera") || document.visibilityState === "hidden") {
        media.getTracks().forEach((track) => track.stop());
        return;
      }
      stream.current = media;
      if (video.current) video.current.srcObject = media;
      setState("on");
    } catch (error) {
      if (request !== generation.current) return;
      setState(error instanceof DOMException && error.name === "NotAllowedError" ? "denied" : "unavailable");
    }
  }
  if (!allowed) return null;
  const actionLabel = state === "on" ? t("stop") : state === "requesting" ? t("cancel") : t("start");
  return <section className="camera-preview" data-state={state} aria-label={t("title")}>
    <div className="camera-preview-picture" data-active={state === "on"}>
      <video ref={video} autoPlay muted playsInline aria-label={t("videoLabel")} />
      {state !== "on" && <KioskIcon name="camera" />}
      {state === "on" && <span className="camera-live"><span />{t("live")}</span>}
    </div>
    <div className="camera-preview-copy">
      <button type="button" className="reception-secondary" aria-label={actionLabel} onClick={() => { if (state === "on" || state === "requesting") { stop(); setState("off"); } else void enable(); }}>
        <KioskIcon name="camera" />{state === "on" ? t("shortStop") : state === "requesting" ? t("shortCancel") : t("shortStart")}
      </button>
      {state === "off" && <span className="camera-preview-status">{t("off")}</span>}
      <details className="camera-preview-info"><summary aria-label={t("info")}><KioskIcon name="help" /><span className="sr-only">{t("info")}</span></summary><p>{t("description")}</p></details>
    </div>
    {(state === "denied" || state === "unavailable") && <p className="camera-preview-error" role="status">{t(state)}</p>}
  </section>;
}
