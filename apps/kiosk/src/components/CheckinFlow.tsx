"use client";

import { Card } from "@careflow/ui";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useState, useSyncExternalStore } from "react";

import { api } from "@/lib/api";
import { readDevice, useKiosk } from "@/lib/session";

import { Keypad } from "./Keypad";

/** After this many failed attempts the kiosk sends the patient to reception. */
export const MAX_FAILURES = 3;

const LINK = "inline-flex min-h-touch items-center rounded-control bg-primary px-6 text-title font-semibold text-on-primary";

const noSubscription = () => () => undefined;

/** The silent keypad -> POST /v1/checkin/keypad (digits only in the body) -> /intake. */
export function CheckinFlow() {
  const t = useTranslations("checkin");
  const tIntake = useTranslations("intake");
  const router = useRouter();
  const { start } = useKiosk();
  const hasDevice = useSyncExternalStore(noSubscription, () => readDevice() !== null, () => true);
  const [failures, setFailures] = useState(0);
  const [working, setWorking] = useState(false);
  const blocked = failures >= MAX_FAILURES;

  async function checkIn(digits: string) {
    const device = readDevice();
    if (!device) return;
    setWorking(true);
    const { data } = await api.POST("/v1/checkin/keypad", {
      body: { clinic_id: device.clinicId, kiosk_id: device.deviceId, digits },
    });
    setWorking(false);
    if (!data) {
      setFailures((count) => count + 1);
      return;
    }
    start(data, {
      question_id: "q_chief_complaint_kiosk",
      text: tIntake("firstQuestion"),
      key: "chief_complaint",
      notice: null,
    });
    router.push("/intake");
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
    </div>
  );
}
