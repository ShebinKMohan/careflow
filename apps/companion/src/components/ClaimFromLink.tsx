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
      initialRevision={visit.revision} initialState={visit.state} onReconnect={() => { reset(); router.push("/"); }} />;
  }
  if (visit.status === "loading") return <p role="status" aria-live="polite">{t("working")}</p>;
  return (
    <Card>
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
