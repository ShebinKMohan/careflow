"use client";

import type { Schema } from "@careflow/contracts";
import { BigButton } from "@careflow/ui";
import { setLocaleCookie } from "@careflow/ui/i18n";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { type FormEvent, useState } from "react";
import { z } from "zod";

import { api } from "@/lib/api";
import { saveSession, saveToken } from "@/lib/session";

import { PhoneIntake } from "./PhoneIntake";

const Claim = z.object({ qr_token: z.string().trim().min(16).max(512) });

const FIELD = "min-h-touch w-full rounded-control border-2 border-border bg-surface px-4 text-body";

/** POST /v1/checkin/qr/claim (no login): the hand-over code, typed in, becomes a phone-channel
 * patient_session token, and the intake continues here as after scanning the QR code (M2-06). */
export function ClaimForm() {
  const t = useTranslations("claim");
  const router = useRouter();
  const [status, setStatus] = useState<"idle" | "working" | "invalid" | "failed">("idle");
  const [result, setResult] = useState<Schema<"CheckinResult"> | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = Claim.safeParse({ qr_token: new FormData(event.currentTarget).get("qr_token") });
    if (!parsed.success) {
      setStatus("invalid");
      return;
    }
    setStatus("working");
    const { data } = await api.POST("/v1/checkin/qr/claim", { body: parsed.data });
    if (!data) {
      setStatus("failed");
      return;
    }
    saveToken(data.session_token);
    saveSession({ sessionId: data.session_id, language: data.language, display: data.patient_display });
    setLocaleCookie(data.language);
    router.refresh();
    setResult(data);
    setStatus("idle");
  }

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

  return (
    <form className="flex flex-col gap-4" onSubmit={submit} noValidate>
      <label className="flex flex-col gap-2 text-label font-semibold">
        {t("code")}
        <input name="qr_token" autoComplete="off" className={FIELD} dir="ltr" />
      </label>
      {status === "invalid" || status === "failed" ? (
        <p role="alert" className="font-semibold text-danger">
          {t(status)}
        </p>
      ) : null}
      <BigButton type="submit" disabled={status === "working"}>
        {status === "working" ? t("working") : t("submit")}
      </BigButton>
    </form>
  );
}
