"use client";

import type { Schema } from "@careflow/contracts";
import { Card } from "@careflow/ui";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useState, useSyncExternalStore } from "react";

import { useKiosk } from "@/lib/session";

import { VitalsPanel } from "./VitalsPanel";

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
 * The patient continues once a reading is in, or skips.
 */
export function VitalsPage() {
  const t = useTranslations("vitals");
  const router = useRouter();
  const params = useSearchParams();
  const { patient } = useKiosk();
  const [measured, setMeasured] = useState(false);
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
  return (
    <div className="flex flex-col gap-6">
      <VitalsPanel sessionId={sessionId} scenario={scenario} autoStart={scenario !== null} onReading={() => setMeasured(true)} />
      <button
        type="button"
        className="min-h-touch rounded-control border-2 border-border bg-surface px-6 text-title font-semibold text-ink"
        onClick={() => router.push("/done")}
      >
        {measured ? t("continue") : t("skip")}
      </button>
    </div>
  );
}
