"use client";

import type { Schema } from "@careflow/contracts";
import { Card, Keypad } from "@careflow/ui";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useState, useSyncExternalStore } from "react";

import { api } from "@/lib/api";
import { readDevice, savePatientSession } from "@/lib/session";

type CheckinResult = Schema<"CheckinResult">;

const LINK = "inline-flex min-h-touch items-center rounded-control bg-primary px-6 text-title font-semibold text-on-primary";

const noSubscription = () => () => undefined;

/** Keypad -> POST /v1/checkin/keypad -> the session (initials only, never a full name). */
export function CheckinFlow() {
  const t = useTranslations("checkin");
  const hasDevice = useSyncExternalStore(noSubscription, () => readDevice() !== null, () => true);
  const [status, setStatus] = useState<"idle" | "working" | "failed">("idle");
  const [result, setResult] = useState<CheckinResult | null>(null);

  async function checkIn(digits: string) {
    const device = readDevice();
    if (!device) return;
    setStatus("working");
    const { data } = await api.POST("/v1/checkin/keypad", {
      body: { clinic_id: device.clinicId, kiosk_id: device.deviceId, digits },
    });
    if (!data) {
      setStatus("failed");
      return;
    }
    savePatientSession({ sessionId: data.session_id, token: data.session_token });
    setResult(data);
    setStatus("idle");
  }

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

  if (result) {
    return (
      <Card title={t("welcome", { initials: result.patient_display })}>
        <p className="mb-6 text-ink-muted">{t("session", { id: result.session_id })}</p>
        <Link href="/intake" className={LINK}>
          {t("continue")}
        </Link>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <p>{t("instructions")}</p>
      <Keypad
        labels={{ display: t("display"), clear: t("clear"), submit: status === "working" ? t("working") : t("submit") }}
        onSubmit={(digits) => void checkIn(digits)}
        disabled={status === "working"}
      />
      {status === "failed" ? (
        <p role="alert" className="font-semibold text-danger">
          {t("failed")}
        </p>
      ) : null}
    </div>
  );
}
