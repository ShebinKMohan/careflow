"use client";

import type { Schema } from "@careflow/contracts";
import { BigButton, Card } from "@careflow/ui";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { type FormEvent, useState } from "react";
import { z } from "zod";

import { api } from "@/lib/api";
import { saveToken } from "@/lib/session";

const Claim = z.object({ qr_token: z.string().trim().min(16).max(512) });

const FIELD = "min-h-touch w-full rounded-control border-2 border-border bg-surface px-4 text-body";

/** POST /v1/checkin/qr/claim (no login): the hand-over code becomes a patient_session token. */
export function ClaimForm() {
  const t = useTranslations("claim");
  const initial = useSearchParams().get("code") ?? "";
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
    setResult(data);
    setStatus("idle");
  }

  if (result) {
    return (
      <Card title={t("welcome", { initials: result.patient_display })}>
        <Link
          href="/status"
          className="inline-flex min-h-touch items-center rounded-control bg-primary px-6 text-title font-semibold text-on-primary"
        >
          {t("status")}
        </Link>
      </Card>
    );
  }

  return (
    <form className="flex flex-col gap-4" onSubmit={submit} noValidate>
      <label className="flex flex-col gap-2 text-label font-semibold">
        {t("code")}
        <input name="qr_token" defaultValue={initial} autoComplete="off" className={FIELD} dir="ltr" />
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
