"use client";

import { Card } from "@careflow/ui";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useState } from "react";

import type { ConsentChoices } from "@/consent/hasConsent";
import { useKiosk } from "@/lib/session";
import { KioskIcon, type KioskIconName } from "./KioskIcon";

const BUTTON = "min-h-touch rounded-control px-6 text-title font-semibold";

/**
 * The consent screen (M2-05), one screen, after the language and before check-in: the camera is
 * processed on the kiosk and never saved; voice is transcribed on the clinic server and no audio
 * is kept; answers go to the clinic team and the clinic record. "Continue without camera and voice"
 * still allows keypad and tap intake. Declining data use sends the patient to reception: nothing is
 * checked in, so nothing is written to the clinic record.
 */
export function ConsentScreen() {
  const t = useTranslations("safety.consent");
  const experience = useTranslations("experience");
  const router = useRouter();
  const { setConsent, wipe } = useKiosk();
  const [declined, setDeclined] = useState(false);

  function choose(choices: ConsentChoices) {
    setConsent(choices);
    router.push("/checkin");
  }

  if (declined) {
    return (
      <Card>
        <p role="alert" className="mb-4 text-title font-semibold" data-testid="consent-declined">
          {t("reception")}
        </p>
        <button
          type="button"
          className={`${BUTTON} border-2 border-primary text-primary`}
          onClick={() => {
            wipe();
            router.push("/");
          }}
        >
          {t("startOver")}
        </button>
      </Card>
    );
  }

  return (
    <div className="consent-screen flex flex-col gap-6">
      <p className="screen-intro">{experience("privacyIntro")}</p>
      <div className="consent-layout">
      <div className="consent-details">
        {([{ key: "camera", icon: "camera" }, { key: "voice", icon: "voice" }, { key: "dataUse", icon: "record" }] as const).map(({ key, icon }) => (
          <section className="consent-detail" key={key}><span className="consent-detail-icon"><KioskIcon name={icon as KioskIconName} /></span><div><h2>{experience(`privacyTitles.${key}`)}</h2><p>{t(key)}</p></div></section>
        ))}
      </div>
      <div className="consent-actions">
        <button
          type="button"
          className={`${BUTTON} bg-primary text-on-primary`}
          onClick={() => choose({ camera: true, voice: true, dataUse: true })}
        >
          {t("agree")}
        </button>
        <button
          type="button"
          className={`${BUTTON} border-2 border-primary text-primary`}
          onClick={() => choose({ camera: false, voice: false, dataUse: true })}
        >
          {t("withoutCameraVoice")}
        </button>
        <button type="button" className={`${BUTTON} border-2 border-border text-ink`} onClick={() => setDeclined(true)}>
          {t("decline")}
        </button>
      </div>
      </div>
    </div>
  );
}
