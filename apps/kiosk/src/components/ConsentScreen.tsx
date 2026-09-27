"use client";

import { Card } from "@careflow/ui";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useState } from "react";

import type { ConsentChoices } from "@/consent/hasConsent";
import { useKiosk } from "@/lib/session";

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
    <div className="flex flex-col gap-6">
      <ul className="flex list-disc flex-col gap-3 ps-6 text-body">
        <li>{t("camera")}</li>
        <li>{t("voice")}</li>
        <li>{t("dataUse")}</li>
      </ul>
      <div className="flex flex-col gap-3">
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
  );
}
