"use client";

import type { Schema } from "@careflow/contracts";
import { useFormatter, useTranslations } from "next-intl";

type Report = Schema<"FeelsWorseSummary">;

/**
 * A waiting patient pressed "I feel worse" on their phone (M2-07): a highlighted, silent banner
 * with the token, the danger answers, the time and the press count, plus a visual pulse (never a
 * sound or a vibration, as with every alert, M1-15). Repeated presses raise the count on the same
 * banner. The note is the patient's own words.
 */
export function FeelsWorseBanner({
  sessionId,
  patient,
  report,
}: {
  sessionId: string;
  /** The token, else the initials; the session card already names the patient. */
  patient?: string;
  report: Report | undefined;
}) {
  const t = useTranslations("nurse.feelsWorse");
  const format = useFormatter();
  if (!report) return null;
  const when = format.dateTime(new Date(report.last_reported_at), { timeStyle: "short", numberingSystem: "latn" });
  const answers = report.danger_answer_codes.map((code) => t(`codes.${code}`));
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-card border-4 border-danger bg-surface p-4"
      data-testid="feels-worse-banner"
      data-session-id={sessionId}
    >
      <span aria-hidden="true" className="mt-1 size-4 shrink-0 rounded-full bg-danger motion-safe:animate-pulse" />
      <div className="flex flex-col gap-1">
        <p className="text-title font-bold text-danger">
          {patient ? t("title", { patient }) : t("titleHere")}{" "}
          <span className="rounded-control border-2 border-danger px-2 text-body" data-testid="feels-worse-count">
            {t("count", { count: report.press_count })}
          </span>
        </p>
        <p>{answers.length ? answers.join(" · ") : t("noAnswers")}</p>
        <p className="text-label text-ink-muted">{t("at", { time: when })}</p>
        {report.note ? (
          <p dir="auto">
            {t("note")} <q>{report.note}</q>
          </p>
        ) : null}
      </div>
    </div>
  );
}
