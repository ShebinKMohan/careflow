"use client";

import { BigButton } from "@careflow/ui";
import { useTranslations } from "next-intl";
import { type FormEvent, useState } from "react";
import { z } from "zod";

import { usePhoneVisit } from "@/lib/usePhoneVisit";

import { PhoneIntake } from "./PhoneIntake";

const Claim = z.object({ qr_token: z.string().trim().min(16).max(512) });
const FIELD = "min-h-touch w-full rounded-control border-2 border-border bg-surface px-4 text-body";

/** The start page also resumes a saved visit, without asking for its consumed QR code again. */
export function ClaimForm() {
  const t = useTranslations("claim");
  const { visit, claim, retry, reset } = usePhoneVisit();
  const [invalid, setInvalid] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = Claim.safeParse({ qr_token: new FormData(event.currentTarget).get("qr_token") });
    setInvalid(!parsed.success);
    if (parsed.success) await claim(parsed.data.qr_token);
  }

  if (visit.status === "ready") {
    return <PhoneIntake sessionId={visit.session.sessionId} token={visit.token}
      language={visit.session.language} display={visit.session.display} initialQuestion={visit.question}
      initialRevision={visit.revision} initialState={visit.state} onReconnect={reset} />;
  }
  const message = invalid ? "invalid" : visit.status === "expired" || visit.status === "failed" || visit.status === "unavailable" ? visit.status : null;
  return (
    <form className="flex flex-col gap-4" onSubmit={submit} noValidate>
      <label className="flex flex-col gap-2 text-label font-semibold">
        {t("code")}
        <input name="qr_token" autoComplete="off" className={FIELD} dir="ltr" />
      </label>
      {message ? <p role="alert" className="font-semibold text-danger">{t(message)}</p> : null}
      {visit.status === "unavailable" ? (
        <BigButton type="button" onClick={() => void retry()}>{t("retry")}</BigButton>
      ) : null}
      <BigButton type="submit" disabled={visit.status === "loading"}>
        {visit.status === "loading" ? t("working") : t("submit")}
      </BigButton>
    </form>
  );
}
