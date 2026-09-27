"use client";

import { Card } from "@careflow/ui";
import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";

import type { LiveStatus } from "@/lib/liveStatus";

/**
 * The waiting room's status card (M2-07): token, room, place in the queue and wait estimate,
 * updated live; never a level or an alert.
 */
export function StatusView({ live }: { live: LiveStatus }) {
  const t = useTranslations("status");
  const tSafety = useTranslations("safety.status");
  const format = useFormatter();
  if (live.status === "loading") return <p aria-busy="true">{t("loading")}</p>;
  if (live.status === "failed") {
    return (
      <p role="alert" className="font-semibold text-danger">
        {t("failed")}
      </p>
    );
  }
  if (live.status === "unauthorized") {
    return (
      <Card>
        <p className="mb-4">{t("unauthorized")}</p>
        <Link href="/" className="inline-flex min-h-touch items-center font-semibold text-primary underline">
          {t("claimLink")}
        </Link>
      </Card>
    );
  }
  const { position, wait_estimate_minutes: minutes, token, room, state } = live.data;
  const updated = format.dateTime(new Date(live.updatedAt), { timeStyle: "short", numberingSystem: "latn" });
  return (
    <Card>
      <div aria-live="polite" className="flex flex-col gap-2" data-testid="waiting-status">
        <p className="text-display font-bold" dir="ltr">
          {token ? token.token_code : null}
        </p>
        <p>{token ? t.rich("token", { code: token.token_code, ltr: (chunks) => <bdi dir="ltr">{chunks}</bdi> }) : t("noToken")}</p>
        {state === "called" && room ? (
          <p className="text-title font-semibold">{t.rich("room", { room, ltr: (chunks) => <bdi dir="ltr">{chunks}</bdi> })}</p>
        ) : null}
        {/* The final states (M2-01): the visit is over, and the reception desk takes any change. */}
        {state === "closed" || state === "no_show" ? (
          <p className="text-title font-semibold" data-testid="visit-ended">
            {tSafety(state === "closed" ? "ended" : "missed")}
          </p>
        ) : null}
        {position !== null ? <p data-testid="position">{t("position", { position })}</p> : null}
        {minutes !== null ? <p>{t("wait", { minutes })}</p> : null}
        <p className="text-label text-ink-muted">
          {t("updated", { time: updated })} {live.live ? null : t("paused")}
        </p>
        {live.stale ? (
          <p role="status" className="font-semibold" data-testid="status-stale">
            {t("stale")}
          </p>
        ) : null}
      </div>
    </Card>
  );
}
