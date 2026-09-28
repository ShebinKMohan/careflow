"use client";

import type { Schema } from "@careflow/contracts";
import { Card } from "@careflow/ui";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useState, useSyncExternalStore } from "react";

import type { MESSAGES } from "@/i18n/messages";
import { useKiosk } from "@/lib/session";

import { VitalsPanel } from "./VitalsPanel";
import { PatientReceipt } from "./PatientReceipt";

const SCENARIOS: readonly Schema<"VitalsScenario">[] = ["normal", "septic", "hypoxic", "tachycardic_child"];

function isScenario(value: string | null): value is Schema<"VitalsScenario"> {
  return value !== null && (SCENARIOS as readonly string[]).includes(value);
}

/** Dev and E2E runs (NEXT_PUBLIC_SIMULATORS=1) may set the simulated device's scenario before
 * the patient reaches this screen, so a journey keeps the kiosk's in-memory session (M1-19). */
export const SIMULATOR_SCENARIO_KEY = "careflow.kiosk.simulatorScenario";
const noSubscription = () => () => undefined;

function storedScenario(): string | null {
  if (process.env.NEXT_PUBLIC_SIMULATORS !== "1") return null;
  try {
    return window.sessionStorage.getItem(SIMULATOR_SCENARIO_KEY);
  } catch {
    return null; // storage blocked
  }
}

/**
 * `/vitals`: the panel for the checked-in patient (`?session=` and `?scenario=`, or the stored
 * simulator scenario, for dev and E2E).
 * The guided demo continues automatically after a fresh reading and its timed sequence.
 */
export function VitalsPage() {
  const t = useTranslations("vitals");
  const experience = useTranslations("experience");
  const reception = useTranslations("reception");
  const router = useRouter();
  const params = useSearchParams();
  const { patient, receptionDraft: draft } = useKiosk();
  const [showSummary, setShowSummary] = useState(false);
  const [returningToHelp, setReturningToHelp] = useState(false);
  const sessionId = params.get("session") ?? patient?.sessionId ?? null;
  const stored = useSyncExternalStore(noSubscription, storedScenario, () => null);
  const requested = params.get("scenario") ?? stored;
  const scenario = isScenario(requested) ? requested : null;

  if (!sessionId) {
    return (
      <Card>
        <p className="mb-4">{t("noSession")}</p>
        <Link href="/checkin" className="inline-flex min-h-touch items-center font-semibold text-primary underline">
          {t("checkinLink")}
        </Link>
      </Card>
    );
  }
  if (showSummary && draft.name && !draft.urgentRequest && patient?.sessionId === sessionId) {
    return <Card>
      <div className="reception-heading"><h1>{t("guide.savedSummary")}</h1><p>{t("guide.alreadySaved")}</p></div>
      <div className="reception-review"><section className="reception-review-section"><dl>
        <div><dt>{reception("name")}</dt><dd>{draft.name}</dd></div>
        <div><dt>{reception("age")}</dt><dd>{draft.age}</dd></div>
        <div><dt>{reception("sex")}</dt><dd>{draft.sex ? reception(`sexOptions.${draft.sex}`) : "—"}</dd></div>
        {draft.phone ? <div><dt>{reception("phone")}</dt><dd dir="ltr">{draft.phone}</dd></div> : null}
        <div><dt>{reception("steps.department")}</dt><dd>{draft.departmentId ? reception(`departments.${draft.departmentId as keyof typeof MESSAGES.en.reception.departments}.title`) : reception("departments.unsure.title")}</dd></div>
        <div><dt>{reception("purpose")}</dt><dd>{draft.purpose ? reception(`purposes.${draft.purpose}`) : "—"}</dd></div>
        <div><dt>{reception("reason")}</dt><dd>{draft.reason}</dd></div>
      </dl></section></div>
      <div className="reception-actions"><button type="button" className="reception-primary" onClick={() => setShowSummary(false)}>{t("guide.returnHealth")}</button></div>
    </Card>;
  }
  return (
    <div className="flex flex-col gap-6">
      <p className="screen-intro">{experience("vitalsIntro")}</p>
      <PatientReceipt compact />
      {!returningToHelp ? <VitalsPanel sessionId={sessionId} patientToken={sessionId === patient?.sessionId ? patient.token : undefined} scenario={scenario} autoStart={scenario !== null} onDone={() => router.push("/done")} /> : null}
      {draft.name && !draft.urgentRequest && patient?.sessionId === sessionId ? <button type="button" className="reception-secondary" onClick={() => setShowSummary(true)}>{t("guide.backSummary")}</button> : null}
      {draft.urgentRequest && patient?.sessionId === sessionId ? <div>
        <button type="button" className="reception-secondary" disabled={returningToHelp} aria-describedby="saved-help-back-description" onClick={() => {
          // Stop the measurement immediately, before the route change completes.
          setReturningToHelp(true);
          router.push("/checkin");
        }}>{reception("back")}</button>
        <p id="saved-help-back-description" className="reception-note">{reception("helpRecorded")}</p>
      </div> : null}
      <button
        type="button"
        className="min-h-touch rounded-control border-2 border-border bg-surface px-6 text-title font-semibold text-ink"
        onClick={() => router.push("/done")}
      >
        {t("guide.skip")}
      </button>
    </div>
  );
}
