"use client";

import { Card } from "@careflow/ui";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { patientApi } from "@/lib/api";
import { useKiosk } from "@/lib/session";
import { KioskIcon } from "./KioskIcon";

const BIG =
  "min-h-touch rounded-control border-2 border-primary px-6 text-title font-semibold text-primary aria-pressed:bg-primary aria-pressed:text-on-primary disabled:opacity-50";
const PRIMARY = "min-h-touch rounded-control bg-primary px-6 text-title font-semibold text-on-primary disabled:opacity-50";
const YEARS = Array.from({ length: 16 }, (_, index) => index); // 0 ("under 1") to 15
const MONTHS = Array.from({ length: 12 }, (_, index) => index);

/**
 * "Who is the patient? Me / My child" (M2-04). "My child" asks the child's age (years, and months
 * under 2) and sends it once to POST /v1/sessions/{id}/patient; the intake then asks the child
 * question bank, and the kiosk skips the adult sensor steps. The kiosk shows no thresholds: the
 * rules engine decides what the answers mean.
 */
export function PatientChoice() {
  const t = useTranslations("safety.child");
  const experience = useTranslations("experience");
  const tIntake = useTranslations("intake");
  const router = useRouter();
  const { patient, setPatientType } = useKiosk();
  const [child, setChild] = useState(false);
  const [years, setYears] = useState<number | null>(null);
  const [months, setMonths] = useState<number | null>(null);
  const [sending, setSending] = useState(false);
  const [failed, setFailed] = useState(false);
  const token = patient?.token;
  const client = useMemo(() => (token ? patientApi(token) : null), [token]);

  if (!patient || !client) {
    return (
      <Card>
        <p className="mb-4">{tIntake("needsCheckin")}</p>
        <Link href="/checkin" className="inline-flex min-h-touch items-center font-semibold text-primary underline">
          {tIntake("checkinLink")}
        </Link>
      </Card>
    );
  }

  const needsMonths = years !== null && years < 2;
  const ready = years !== null && (!needsMonths || months !== null);

  async function sendChild() {
    if (!patient || !client || years === null || !ready) return;
    setSending(true);
    setFailed(false);
    const result = await client.POST("/v1/sessions/{session_id}/patient", {
      params: { path: { session_id: patient.sessionId } },
      body: { patient_type: "child", age_years: years, ...(needsMonths ? { age_months: months ?? 0 } : {}) },
    }).catch(() => null);
    setSending(false);
    if (!result?.data) {
      setFailed(true);
      return;
    }
    setPatientType("child");
    router.push("/intake");
  }

  const greeting = <p className="text-label text-ink-muted">{tIntake.rich("greeting", { initials: patient.display, ltr: (chunks) => <bdi dir="ltr">{chunks}</bdi> })}</p>;

  if (!child) {
    return (
      <div className="flex flex-col gap-4">
        {greeting}
        <p className="screen-intro">{experience("patientIntro")}</p>
        <div className="patient-options">
        <button type="button" className="patient-option" aria-label={t("me")} onClick={() => router.push("/intake")}>
          <KioskIcon name="person" /><strong>{t("me")}</strong><span>{experience("adultDescription")}</span>
        </button>
        <button type="button" className="patient-option" aria-label={t("myChild")} onClick={() => setChild(true)} data-testid="patient-child">
          <KioskIcon name="child" /><strong>{t("myChild")}</strong><span>{experience("childDescription")}</span>
        </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      {greeting}
      <fieldset className="flex flex-col gap-3">
        <legend className="mb-2 text-title font-semibold">{t("ageTitle")}</legend>
        <div className="grid grid-cols-4 gap-3" role="group" aria-label={t("yearsLabel")}>
          {YEARS.map((value) => (
            <button
              key={value}
              type="button"
              className={BIG}
              aria-pressed={years === value}
              onClick={() => {
                setYears(value);
                if (value >= 2) setMonths(null);
              }}
            >
              {value === 0 ? t("underOne") : <bdi dir="ltr">{value}</bdi>}
            </button>
          ))}
        </div>
      </fieldset>
      {needsMonths ? (
        <fieldset className="flex flex-col gap-3">
          <legend className="mb-2 text-title font-semibold">{t("monthsLabel")}</legend>
          <div className="grid grid-cols-4 gap-3" role="group" aria-label={t("monthsLabel")}>
            {MONTHS.map((value) => (
              <button key={value} type="button" className={BIG} aria-pressed={months === value} onClick={() => setMonths(value)}>
                <bdi dir="ltr">{value}</bdi>
              </button>
            ))}
          </div>
        </fieldset>
      ) : null}
      {failed ? (
        <p role="alert" className="font-semibold">
          {t("failed")}
        </p>
      ) : null}
      <button type="button" className={PRIMARY} disabled={!ready || sending} onClick={() => void sendChild()}>
        {t("continue")}
      </button>
    </div>
  );
}
