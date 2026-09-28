"use client";

import { BigButton } from "@careflow/ui";
import { useTranslations } from "next-intl";
import { type FormEvent, useState } from "react";
import { z } from "zod";

import { usePhoneVisit } from "@/lib/usePhoneVisit";

import { PhoneIntake } from "./PhoneIntake";
import { CompanionIcon } from "./CompanionIcon";

const Claim = z.object({ qr_token: z.string().trim().min(16).max(512) });
const FIELD = "companion-input companion-code-input";

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
      initialRevision={visit.revision} initialState={visit.state} childSession={visit.session.patientType === "child"} onReconnect={reset} />;
  }
  const message = invalid ? "invalid" : visit.status === "expired" || visit.status === "failed" || visit.status === "unavailable" ? visit.status : null;
  return (
    <div className="companion-connect">
      <div className="companion-page-heading companion-welcome">
        <span className="companion-hero-icon"><CompanionIcon name="phone" /></span>
        <span className="companion-eyebrow">{t("eyebrow")}</span>
        <h1>{t("title")}</h1>
        <p>{t("intro")}</p>
      </div>
      <section className="companion-start-guide"><CompanionIcon name="phone" /><div><h2>{t("scanTitle")}</h2><p>{t("scanHelp")}</p></div></section>
      <section className="companion-panel companion-connect-panel">
      <div className="companion-section-title"><CompanionIcon name="shield" /><h2>{t("connectTitle")}</h2></div>
      <p className="companion-muted">{t("connectHelp")}</p>
      <form className="companion-form" onSubmit={submit} noValidate>
      <label className="flex flex-col gap-2 text-label font-semibold">
        {t("code")}
        <input name="qr_token" autoComplete="off" autoCapitalize="none" spellCheck={false} maxLength={512} aria-invalid={invalid} aria-describedby="code-hint" className={FIELD} dir="ltr" placeholder={t("placeholder")} />
      </label>
      <p id="code-hint" className="companion-field-hint">{t("codeHint")}</p>
      {message ? <p role="alert" className="companion-notice companion-error">{t(message)}</p> : null}
      {visit.status === "unavailable" ? (
        <BigButton type="button" onClick={() => void retry()}>{t("retry")}</BigButton>
      ) : null}
      <BigButton type="submit" disabled={visit.status === "loading"}>
        {visit.status === "loading" ? t("working") : t("submit")}
      </BigButton>
      </form>
      </section>
      <ol className="companion-benefits">
        <li><CompanionIcon name="check" /><span>{t("benefitIntake")}</span></li>
        <li><CompanionIcon name="clock" /><span>{t("benefitQueue")}</span></li>
        <li><CompanionIcon name="message" /><span>{t("benefitClinic")}</span></li>
      </ol>
    </div>
  );
}
