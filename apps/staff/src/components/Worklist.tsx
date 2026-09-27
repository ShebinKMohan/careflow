"use client";

import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";
import { useCallback, useEffect, useRef } from "react";

import { type AlertRaised, useLiveWorklist } from "@/lib/useLiveWorklist";
import { useSessionAlerts } from "@/lib/useSessionAlerts";

import { ApiStatus } from "./ApiStatus";
import { AlertBanner } from "./nurse/AlertBanner";
import { FeelsWorseBanner } from "./nurse/FeelsWorseBanner";
import { LevelChip } from "./nurse/LevelChip";

const CELL = "border-b border-border px-3 py-3 text-start align-top";

/**
 * The nurse worklist (M1-15): active alerts first as sticky banners, then the sessions sorted
 * danger first. Live over /v1/ws/staff; while that is down, "Live updates paused" and a poll every
 * 5 s. A level is only ever "Suggested" until the nurse confirms it (safety rule 3).
 */
export function Worklist() {
  const t = useTranslations("nurse");
  const tAlerts = useTranslations("alerts");
  const format = useFormatter();
  const pushRef = useRef<(raised: AlertRaised) => void>(() => undefined);
  const onAlert = useCallback((raised: AlertRaised) => pushRef.current(raised), []);
  const { state, live } = useLiveWorklist(onAlert);
  const items = state.status === "ready" ? state.data : null;
  const { sessions, push, updateTask } = useSessionAlerts(items);

  useEffect(() => {
    pushRef.current = push;
  }, [push]);

  return (
    <ApiStatus state={state}>
      {(rows) => (
        <div className="flex flex-col gap-6">
          <p
            className={`w-fit rounded-control border-2 px-3 py-1 font-semibold ${
              live === "paused" ? "border-danger text-danger" : "border-border"
            }`}
            data-testid="live-status"
          >
            {live === "live" ? t("live") : live === "paused" ? t("paused") : t("connecting")}
          </p>
          {rows.some((row) => row.feels_worse) ? (
            <div className="flex flex-col gap-3" role="region" aria-label={t("feelsWorse.region")}>
              {rows
                .filter((row) => row.feels_worse)
                .sort((a, b) => (b.feels_worse?.last_reported_at ?? "").localeCompare(a.feels_worse?.last_reported_at ?? ""))
                .map((row) => (
                  <FeelsWorseBanner key={row.session_id} item={row} />
                ))}
            </div>
          ) : null}
          {sessions.length ? (
            <div className="sticky top-0 z-10 flex flex-col gap-3" aria-label={tAlerts("title")} role="region">
              {sessions.flatMap((session) =>
                session.alerts.map((alert) => (
                  <AlertBanner
                    key={alert.alert_id}
                    alert={alert}
                    facts={session.facts}
                    tasks={session.tasks}
                    patient={session.patientDisplay}
                    onTaskDone={updateTask}
                  />
                )),
              )}
            </div>
          ) : null}
          {rows.length === 0 ? (
            <p>{t("empty")}</p>
          ) : (
            // The table scrolls inside its own box on a narrow screen, never the whole page.
            <div className="overflow-x-auto">
              <table className="w-full border-collapse bg-surface">
                <thead>
                  <tr>
                    <th className={CELL}>{t("arrived")}</th>
                    <th className={CELL}>{t("patient")}</th>
                    <th className={CELL}>{t("ageSex")}</th>
                    <th className={CELL}>{t("complaint")}</th>
                    <th className={CELL}>{t("alerts")}</th>
                    <th className={CELL}>{t("levelColumn")}</th>
                    <th className={CELL}>{t("state")}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((item) => (
                    <tr key={item.session_id} data-session={item.session_id}>
                      <td className={CELL}>{format.dateTime(new Date(item.arrived_at), { timeStyle: "short", numberingSystem: "latn" })}</td>
                      <td className={`${CELL} font-semibold`}>
                        <Link href={`/nurse/${encodeURIComponent(item.session_id)}`} className="text-primary underline">
                          {item.patient_display}
                        </Link>
                      </td>
                      <td className={CELL}>
                        {[
                          item.age_years === undefined ? null : t("age", { age: item.age_years }),
                          item.sex ? t(`sex.${item.sex}`) : null,
                        ]
                          .filter(Boolean)
                          .join(", ") || t("unknown")}
                      </td>
                      <td className={CELL}>{item.complaint ?? t("unknown")}</td>
                      <td className={CELL}>
                        {item.alerts.map((alert) => (
                          <span
                            key={alert.alert_id}
                            className={`mb-1 me-2 inline-block rounded-control px-2 text-label ${
                              alert.severity === "critical"
                                ? "bg-danger text-on-danger"
                                : "border-2 border-danger text-danger"
                            }`}
                          >
                            {tAlerts(`severity.${alert.severity}`)}: {tAlerts(`rules.${alert.rule_id}`)}
                          </span>
                        ))}
                      </td>
                      <td className={CELL}>
                        <LevelChip item={item} />
                      </td>
                      <td className={CELL}>{t(`states.${item.state}`)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </ApiStatus>
  );
}
