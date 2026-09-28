"use client";

import type { Schema } from "@careflow/contracts";
import { Card } from "@careflow/ui";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { CONSENT_VERSION, type ConsentChoices, markConsentSaved } from "@/consent/hasConsent";
import { type CardPayload, cardReader, simulatorsEnabled } from "@/devices/card-reader";
import { api, patientApi } from "@/lib/api";
import { readDevice, useKiosk } from "@/lib/session";

import { Keypad } from "./Keypad";
import { SimulatedIdReader } from "./SimulatedIdReader";

/** After this many failed attempts the kiosk sends the patient to reception. */
export const MAX_FAILURES = 3;

const LINK = "inline-flex min-h-touch items-center rounded-control bg-primary px-6 text-title font-semibold text-on-primary";

const noSubscription = () => () => undefined;

/**
 * Check-in: the silent keypad (POST /v1/checkin/keypad, digits only in the body) or a tap of the
 * mock ID card (POST /v1/checkin/id-tap, M2-05). The consent screen comes first. Once the session
 * exists, its choices are recorded (POST /v1/sessions/{id}/consent), and the kiosk goes to /patient
 * only when the API has saved them. A failed save says so and offers to try again or start again:
 * the kiosk never carries on as if the choices were saved, and without a record the microphone
 * stays off (`hasConsent`, the API and the voice service all check it). An API that cannot be
 * reached at check-in says so too, and the patient can try again: it is not a failed attempt.
 */
type Unsaved = { data: Schema<"CheckinResult">; choices: ConsentChoices };

async function saveConsent({ data, choices }: Unsaved): Promise<boolean> {
  try {
    const { error, response } = await patientApi(data.session_token).POST("/v1/sessions/{session_id}/consent", {
      params: { path: { session_id: data.session_id } },
      body: {
        camera: choices.camera,
        voice: choices.voice,
        data_use: choices.dataUse,
        consent_version: CONSENT_VERSION,
      },
    });
    return error === undefined && response.ok;
  } catch {
    return false; // the API could not be reached
  }
}

export function CheckinFlow() {
  const t = useTranslations("checkin");
  const locale = useLocale() === "ar" ? "ar" : "en";
  const tIntake = useTranslations("intake");
  const tConsent = useTranslations("safety.consent");
  const router = useRouter();
  const { start, consent, wipe } = useKiosk();
  const hasDevice = useSyncExternalStore(noSubscription, () => readDevice() !== null, () => true);
  const [failures, setFailures] = useState(0);
  const [working, setWorking] = useState(false);
  const [cardMessage, setCardMessage] = useState<"notFound" | "reception" | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [unsaved, setUnsaved] = useState<Unsaved | null>(null);
  const leaving = useRef(false); // "Start again" goes home: not back to the consent screen
  const blocked = failures >= MAX_FAILURES;

  useEffect(() => {
    if (hasDevice && consent === null && !leaving.current) router.replace("/consent");
  }, [hasDevice, consent, router]);

  const begin = useCallback(
    async (data: Schema<"CheckinResult">, choices: ConsentChoices) => {
      start(data, {
        question_id: "q_chief_complaint_kiosk",
        text: tIntake("firstQuestion"),
        key: "chief_complaint",
        notice: null,
      });
      if (!(await saveConsent({ data, choices }))) {
        setUnsaved({ data, choices });
        return;
      }
      markConsentSaved();
      router.push("/patient");
    },
    [start, router, tIntake],
  );

  async function retrySave() {
    if (!unsaved) return;
    setWorking(true);
    const ok = await saveConsent(unsaved);
    setWorking(false);
    if (!ok) return; // the message stays; the patient may try again or start again
    setUnsaved(null);
    markConsentSaved();
    router.push("/patient");
  }

  async function checkIn(digits: string) {
    const device = readDevice();
    if (!device || !consent) return;
    setWorking(true);
    setUnreachable(false);
    const answer = await api
      .POST("/v1/checkin/keypad", {
        body: { clinic_id: device.clinicId, kiosk_id: device.deviceId, digits, language: locale },
      })
      .catch(() => null); // the API could not be reached
    setWorking(false);
    if (answer === null) {
      setUnreachable(true);
      return;
    }
    const { data } = answer;
    if (!data) {
      setFailures((count) => count + 1);
      return;
    }
    await begin(data, consent);
  }

  const checkInWithCard = useCallback(
    async (card: CardPayload) => {
      const device = readDevice();
      if (!device || !consent) return;
      setWorking(true);
      setCardMessage(null);
      setUnreachable(false);
      const answer = await api
        .POST("/v1/checkin/id-tap", {
          body: { clinic_id: device.clinicId, kiosk_id: device.deviceId, mock_card_payload: card, language: locale },
        })
        .catch(() => null); // the API could not be reached
      setWorking(false);
      if (answer === null) {
        setUnreachable(true);
        return;
      }
      const { data, response } = answer;
      if (!data) {
        setCardMessage(response.status === 409 ? "reception" : "notFound");
        return;
      }
      await begin(data, consent);
    },
    [consent, locale, begin],
  );

  useEffect(() => cardReader.onTap((card) => void checkInWithCard(card)), [checkInWithCard]);

  if (!hasDevice) {
    return (
      <Card>
        <p className="mb-4">{t("needsSetup")}</p>
        <Link href="/setup" className={LINK}>
          {t("setupLink")}
        </Link>
      </Card>
    );
  }
  if (consent === null) return null; // on the way to the consent screen
  if (unsaved) {
    return (
      <Card>
        <p role="alert" className="mb-4 text-title font-semibold text-danger" data-testid="consent-not-saved">
          {tConsent("saveFailed")}
        </p>
        <div className="flex flex-wrap gap-3">
          <button
            type="button"
            className="min-h-touch rounded-control bg-primary px-6 text-title font-semibold text-on-primary"
            disabled={working}
            onClick={() => void retrySave()}
          >
            {tConsent("retry")}
          </button>
          <button
            type="button"
            className="min-h-touch rounded-control border-2 border-primary px-6 text-title font-semibold text-primary"
            onClick={() => {
              leaving.current = true;
              wipe();
              router.push("/");
            }}
          >
            {tConsent("startOver")}
          </button>
        </div>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <p>{t("instructions")}</p>
      <Keypad
        key={failures}
        labels={{
          display: t("display"),
          backspace: t("backspace"),
          clear: t("clear"),
          submit: working ? t("working") : t("submit"),
        }}
        onSubmit={(digits) => void checkIn(digits)}
        disabled={working || blocked}
      />
      {failures > 0 ? (
        <p role="alert" className="text-title font-semibold text-danger">
          {blocked ? t("seeReception") : t("failed")}
        </p>
      ) : null}
      {unreachable ? (
        <p role="alert" className="text-title font-semibold text-danger" data-testid="checkin-unreachable">
          {t("unreachable")}
        </p>
      ) : null}
      <p className="text-body">{t("tapCard")}</p>
      {cardMessage ? (
        <p role="alert" className="text-title font-semibold text-danger" data-testid="card-message">
          {cardMessage === "reception" ? t("cardReception") : t("cardNotFound")}
        </p>
      ) : null}
      {simulatorsEnabled() ? <SimulatedIdReader /> : null}
    </div>
  );
}
