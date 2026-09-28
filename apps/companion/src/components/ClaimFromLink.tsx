"use client";

import { Card } from "@careflow/ui";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

import { usePhoneVisit } from "@/lib/usePhoneVisit";

import { PhoneIntake } from "./PhoneIntake";

/** Claim a fragment code once, or recover this tab's visit from the server after a reload. */
export function ClaimFromLink() {
  const t = useTranslations("claim");
  const { visit, retry, reset } = usePhoneVisit(true);
  const router = useRouter();

  if (visit.status === "ready") {
    return <PhoneIntake sessionId={visit.session.sessionId} token={visit.token}
      language={visit.session.language} display={visit.session.display} initialQuestion={visit.question}
      initialRevision={visit.revision} initialState={visit.state} childSession={visit.session.patientType === "child"} onReconnect={() => { reset(); router.push("/"); }} />;
  }
  if (visit.status === "loading") return <div className="companion-loading" role="status" aria-live="polite"><span className="companion-spinner" aria-hidden="true" /><h1>{t("working")}</h1><p>{t("connectHelp")}</p></div>;
  return (
    <Card className="companion-panel"><h1 className="companion-recovery-title">{t("title")}</h1>
      <p role="alert" className="mb-4">{t(visit.status === "empty" ? "noCode" : visit.status)}</p>
      {visit.status === "unavailable" ? (
        <button type="button" className="min-h-touch px-4 font-semibold text-primary underline" onClick={() => void retry()}>
          {t("retry")}
        </button>
      ) : null}
      <Link href="/" className="inline-flex min-h-touch items-center font-semibold text-primary underline">
        {t("enterCode")}
      </Link>
    </Card>
  );
}
