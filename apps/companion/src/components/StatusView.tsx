"use client";

import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";

import type { LiveStatus } from "@/lib/liveStatus";

import { CompanionIcon } from "./CompanionIcon";

/** Queue values and the visit stage come only from the server; estimates are labelled as such. */
export function StatusView({ live }: { live: LiveStatus }) {
  const t = useTranslations("status");
  const tSafety = useTranslations("safety.status");
  const format = useFormatter();
  if (live.status === "loading") return <div className="companion-panel companion-loading" role="status" aria-busy="true"><span className="companion-spinner" aria-hidden="true" /><p>{t("loading")}</p></div>;
  if (live.status === "failed") {
    return <div className="companion-panel companion-error" role="alert"><CompanionIcon name="alert" /><h2>{t("failed")}</h2><p>{t("receptionHelp")}</p><button type="button" className="companion-choice" onClick={() => window.location.reload()}>{t("retry")}</button></div>;
  }
  if (live.status === "unauthorized") {
    return <section className="companion-panel"><div className="companion-section-title"><CompanionIcon name="phone" /><h2>{t("unauthorized")}</h2></div><Link href="/" className="companion-primary">{t("claimLink")}</Link></section>;
  }
  const { position, wait_estimate_minutes: minutes, token, room, state } = live.data;
  const updated = format.dateTime(new Date(live.updatedAt), { timeStyle: "short", numberingSystem: "latn" });
  const ended = state === "closed" || state === "no_show";
  const title = state === "called" ? "calledTitle" : state === "arrived" ? "arrivedTitle" : state === "closed" ? "endedTitle" : state === "no_show" ? "missedTitle" : state === "queued" ? "waitingTitle" : state === "awaiting_nurse" ? "reviewTitle" : state === "intake" ? "intakeTitle" : "intakeTitle";
  return (
    <section className={`companion-panel companion-status-card ${state === "called" ? "companion-called" : ""}`}>
      <div className="companion-status-top"><h2><CompanionIcon name={state === "called" ? "arrow" : ended ? "check" : "clock"} />{t(title)}</h2><span className={`companion-connection ${live.live && !live.stale ? "is-live" : ""}`}><span aria-hidden="true" />{t(live.live && !live.stale ? "live" : "checking")}</span></div>
      <div aria-live="polite" className="companion-ticket" data-testid="waiting-status">
        <span className="companion-eyebrow">{t("ticketLabel")}</span>
        <p className="companion-ticket-number" dir="ltr">{token ? token.token_code : "—"}</p>
        <p className="companion-ticket-label">{token ? t.rich("token", { code: token.token_code, ltr: (chunks) => <bdi dir="ltr">{chunks}</bdi> }) : t("noToken")}</p>
        {state === "called" && room ? <p className="companion-room">{t.rich("room", { room, ltr: (chunks) => <bdi dir="ltr">{chunks}</bdi> })}</p> : null}
        {ended ? <p className="companion-notice" data-testid="visit-ended">{tSafety(state === "closed" ? "ended" : "missed")}</p> : null}
        {position !== null || minutes !== null ? <div className="companion-queue-facts">
          {position !== null ? <p data-testid="position">{t("position", { position })}</p> : null}
          {minutes !== null ? <p>{t("wait", { minutes })}</p> : null}
        </div> : null}
        <p className="companion-update">{t("updated", { time: updated })} {live.live ? null : t("paused")}</p>
        {live.stale ? <p role="status" className="companion-notice" data-testid="status-stale">{t("stale")}</p> : null}
      </div>
      {!ended && state !== "called" && state !== "arrived" ? <div className="companion-next"><CompanionIcon name="shield" /><div><h3>{t("nextTitle")}</h3><p>{t(state === "queued" ? "nextWaiting" : state === "intake" ? "nextIntake" : "nextReview")}</p>{state === "intake" ? <Link href="/" className="companion-text-link">{t("resumeIntake")}</Link> : null}</div></div> : null}
      {minutes !== null ? <p className="companion-wait-note">{t("waitNote")}</p> : null}
    </section>
  );
}
