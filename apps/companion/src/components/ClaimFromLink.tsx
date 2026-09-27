"use client";

import type { Schema } from "@careflow/contracts";
import { Card } from "@careflow/ui";
import { setLocaleCookie } from "@careflow/ui/i18n";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useState, useSyncExternalStore } from "react";

import { api } from "@/lib/api";
import { saveSession, saveToken } from "@/lib/session";

import { PhoneIntake } from "./PhoneIntake";

const noSubscription = () => () => undefined;

/** The hand-over code from the URL fragment (`/claim#t=<code>`): the phone never sends a fragment
 * in a request, so the code reaches the API only in the claim's POST body. */
function codeFromHash(): string {
  return new URLSearchParams(window.location.hash.slice(1)).get("t") ?? "";
}

/**
 * The QR hand-over's landing page (M2-06). It takes the code from the fragment, clears it from the
 * address bar and history, and claims the session (POST /v1/checkin/qr/claim). The phone-channel
 * token and the session stay in this tab's sessionStorage only. The intake then continues here,
 * in the session's language, exactly where the kiosk stopped.
 */
export function ClaimFromLink() {
  const t = useTranslations("claim");
  const router = useRouter();
  const code = useSyncExternalStore(noSubscription, codeFromHash, () => "");
  const [result, setResult] = useState<Schema<"CheckinResult"> | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!code) return;
    window.history.replaceState(null, "", window.location.pathname);
    void api.POST("/v1/checkin/qr/claim", { body: { qr_token: code } }).then(({ data }) => {
      if (!data) {
        setFailed(true);
        return;
      }
      saveToken(data.session_token);
      saveSession({ sessionId: data.session_id, language: data.language, display: data.patient_display });
      setLocaleCookie(data.language);
      router.refresh(); // the rest of the visit in the session's language
      setResult(data);
    });
  }, [code, router]);

  if (result) {
    return (
      <PhoneIntake
        sessionId={result.session_id}
        token={result.session_token}
        language={result.language}
        display={result.patient_display}
        initialQuestion={result.first_question ?? null}
      />
    );
  }
  if (failed || !code) {
    return (
      <Card>
        <p role="alert" className="mb-4">
          {failed ? t("failed") : t("noCode")}
        </p>
        <Link href="/" className="font-semibold text-primary underline">
          {t("enterCode")}
        </Link>
      </Card>
    );
  }
  return (
    <p role="status" aria-live="polite">
      {t("working")}
    </p>
  );
}
