"use client";

import type { Schema } from "@careflow/contracts";
import { Card } from "@careflow/ui";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useSyncExternalStore } from "react";

import { readPatientSession } from "@/lib/session";

import { VitalsPanel } from "./VitalsPanel";

const SCENARIOS: readonly Schema<"VitalsScenario">[] = ["normal", "septic", "hypoxic", "tachycardic_child"];

function isScenario(value: string | null): value is Schema<"VitalsScenario"> {
  return value !== null && (SCENARIOS as readonly string[]).includes(value);
}

const noSubscription = () => () => undefined;

/** `/vitals`: the panel for the checked-in session (`?session=` and `?scenario=` for dev and E2E). */
export function VitalsPage() {
  const t = useTranslations("vitals");
  const params = useSearchParams();
  const stored = useSyncExternalStore(noSubscription, () => readPatientSession()?.sessionId ?? null, () => null);
  const sessionId = params.get("session") ?? stored;
  const requested = params.get("scenario");
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
  return <VitalsPanel sessionId={sessionId} scenario={scenario} autoStart={scenario !== null} />;
}
