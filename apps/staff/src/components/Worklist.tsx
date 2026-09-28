"use client";

import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";
import { useCallback, useEffect, useRef, useState } from "react";

import { type AlertRaised, useLiveWorklist } from "@/lib/useLiveWorklist";
import { useSessionAlerts } from "@/lib/useSessionAlerts";

import { ApiStatus } from "./ApiStatus";
import { ReceptionPreference } from "./ReceptionSummary";
import { AlertBanner } from "./nurse/AlertBanner";
import { FeelsWorseBanner } from "./nurse/FeelsWorseBanner";
import { LevelChip } from "./nurse/LevelChip";
import { MedicalQuestions } from "./nurse/MedicalQuestions";
import { QueueControls } from "./nurse/QueueControls";

const visitReference = (sessionId: string) => `CF-${sessionId.replaceAll("-", "").slice(0, 8).toUpperCase()}`;

const CELL = "border-b border-border px-3 py-3 text-start align-top";

/**
 * The nurse worklist (M1-15): active alerts first as sticky banners, then the sessions sorted
 * danger first. Live over /v1/ws/staff; while that is down, "Live updates paused" and a poll every
 * 5 s. A level is only ever "Suggested" until the nurse confirms it (safety rule 3).
 */
export function Worklist() {
  const t = useTranslations("nurse");
  const tools = useTranslations("worklistTools");
  const reception = useTranslations("reception");
  const help = useTranslations("urgentHelp");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"all" | "review" | "alerts" | "queue">("all");
  const tAlerts = useTranslations("safety.alerts");
  const tSafety = useTranslations("safety");
  const format = useFormatter();
  const pushRef = useRef<(raised: AlertRaised) => void>(() => undefined);
  const onAlert = useCallback((raised: AlertRaised) => pushRef.current(raised), []);
  const { state, live, refresh } = useLiveWorklist(onAlert);
  const items = state.status === "ready" ? state.data : null;
  const { sessions, push, updateTask } = useSessionAlerts(items);

  useEffect(() => {
    pushRef.current = push;
  }, [push]);

  return (
    <ApiStatus state={state}>
      {(rows) => {
        const reviewing = (state: string) => state === "awaiting_nurse";
        const queued = (state: string) => ["queued", "called", "arrived"].includes(state);
        const visibleRows = rows.filter((row) => {
          const matchesQuery = [row.patient_display, row.complaint, row.token_code, row.reception_context?.department_name, row.reception_context?.doctor_name, row.reception_context?.reason, row.urgent_help ? visitReference(row.session_id) : null].filter(Boolean).join(" ").toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
          return matchesQuery && (filter === "all" || (filter === "review" && reviewing(row.state)) || (filter === "alerts" && (row.alerts.length > 0 || row.urgent_help)) || (filter === "queue" && queued(row.state)));
        });
        return (
        <div className="staff-worklist flex flex-col gap-6">
          <div className="staff-worklist-status">
          <p
            className={`w-fit rounded-control border-2 px-3 py-1 font-semibold ${
              live === "paused" ? "border-danger text-danger" : "border-border"
            }`}
            data-testid="live-status"
          >
            {live === "live" ? t("live") : live === "paused" ? t("paused") : t("connecting")}
          </p>
          <button type="button" className="staff-text-button" onClick={() => void refresh()}>{tools("refresh")} <span aria-hidden="true">↻</span></button>
          </div>
          <div className="staff-metrics">
            {[
              { label: tools("total"), value: rows.length },
              { label: tools("review"), value: rows.filter((row) => reviewing(row.state)).length },
              { label: tools("alerts"), value: rows.filter((row) => row.alerts.length > 0 || row.urgent_help).length, alert: true },
              { label: tools("inQueue"), value: rows.filter((row) => queued(row.state)).length },
            ].map((metric) => <div className="staff-metric" key={metric.label} data-alert={metric.alert && metric.value > 0 ? "true" : undefined}><span>{metric.label}</span><strong>{metric.value}</strong></div>)}
          </div>
          {rows.some((row) => row.feels_worse) ? (
            <div className="flex flex-col gap-3" role="region" aria-label={tSafety("feelsWorse.region")}>
              {rows
                .filter((row) => row.feels_worse)
                .sort((a, b) => (b.feels_worse?.last_reported_at ?? "").localeCompare(a.feels_worse?.last_reported_at ?? ""))
                .map((row) => (
                  <FeelsWorseBanner
                    key={row.session_id}
                    sessionId={row.session_id}
                    patient={row.token_code ?? row.patient_display}
                    report={row.feels_worse}
                  />
                ))}
            </div>
          ) : null}
          {rows.some((row) => row.medical_questions?.length) ? (
            <div className="flex flex-col gap-3" role="region" aria-label={tSafety("medicalQuestions.region")}>
              {rows
                .filter((row) => row.medical_questions?.length)
                .sort((a, b) =>
                  (b.medical_questions?.[0]?.asked_at ?? "").localeCompare(a.medical_questions?.[0]?.asked_at ?? ""),
                )
                .map((row) => (
                  <MedicalQuestions key={row.session_id} item={row} />
                ))}
            </div>
          ) : null}
          {sessions.length ? (
            // Sticky, but never more than 45% of the screen: with many alerts the region scrolls on its
            // own, so the patient list and the queue controls below stay usable.
            <div className="sticky top-0 z-10 flex max-h-[45vh] flex-col gap-3 overflow-y-auto" aria-label={tAlerts("title")} role="region">
              {sessions.flatMap((session) =>
                session.alerts.map((alert) => (
                  <AlertBanner
                    key={alert.alert_id}
                    alert={alert}
                    facts={session.facts}
                    tasks={session.tasks}
                    patient={session.patientDisplay}
                    onTaskDone={updateTask}
                    afterDecision={session.decidedAt !== undefined}
                  />
                )),
              )}
            </div>
          ) : null}
          {rows.some((row) => row.urgent_help) ? <section className="staff-urgent-requests" aria-label={help("title")} data-testid="urgent-help-requests"><h2>{help("title")}</h2><p>{help("note")}</p>{rows.filter((row) => row.urgent_help).map((row) => <div key={row.session_id} className="staff-urgent-request"><div><strong>{help("unknownPatient")}</strong><span className="staff-urgent-reference" dir="ltr">{visitReference(row.session_id)}</span><span>{help(row.urgent_help!.patient_type === "child" ? "child" : "adult")}</span><span>{help("requested", { time: format.dateTime(new Date(row.urgent_help!.requested_at), { timeStyle: "short", numberingSystem: "latn" }) })}</span></div>{row.urgent_help!.emergency_symptoms.length ? <ul>{row.urgent_help!.emergency_symptoms.map((symptom) => <li key={symptom}>{reception(`symptoms.${symptom}`)}</li>)}</ul> : <p>{help("noSymptoms")}</p>}<Link href={`/nurse/${encodeURIComponent(row.session_id)}`} className="staff-text-button">{help("open")}</Link></div>)}</section> : null}
          <QueueControls rows={rows} onChanged={() => void refresh()} />
          <section className="staff-patient-panel" aria-label={t("title")}>
          <div className="staff-worklist-toolbar">
            <label className="staff-search"><span aria-hidden="true">⌕</span><span className="sr-only">{tools("search")}</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={tools("searchPlaceholder")} /></label>
            <div className="staff-filters" role="group" aria-label={tools("filter")}>
              {([ ["all", "all"], ["review", "needsReview"], ["alerts", "alerted"], ["queue", "queued"] ] as const).map(([value, key]) => <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)}>{tools(key)}</button>)}
            </div>
          </div>
          <div className="staff-list-caption"><span>{tools("results", { count: visibleRows.length })}</span><span>{tools("priority")}</span></div>
          {visibleRows.length === 0 ? (
            <div className="staff-empty"><p>{rows.length === 0 ? t("empty") : tools("noMatches")}</p>{rows.length > 0 ? <button type="button" className="staff-text-button" onClick={() => { setQuery(""); setFilter("all"); }}>{tools("clear")}</button> : null}</div>
          ) : (
            // The table scrolls inside its own box on a narrow screen, never the whole page.
            <div className="staff-table-wrap">
              <table className="staff-table w-full border-collapse bg-surface">
                <thead>
                  <tr>
                    <th className={CELL}>{t("arrived")}</th>
                    <th className={CELL}>{t("patient")}</th>
                    <th className={CELL}>{t("ageSex")}</th>
                    <th className={CELL}>{t("complaint")}</th>
                    <th className={CELL}>{reception("column")}</th>
                    <th className={CELL}>{t("alerts")}</th>
                    <th className={CELL}>{t("levelColumn")}</th>
                    <th className={CELL}>{t("state")}</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.map((item) => (
                    <tr key={item.session_id} data-session={item.session_id} data-alert={item.alerts.length || item.urgent_help ? "true" : undefined}>
                      <td className={CELL}>{format.dateTime(new Date(item.arrived_at), { timeStyle: "short", numberingSystem: "latn" })}</td>
                      <td className={`${CELL} font-semibold`}>
                        <Link href={`/nurse/${encodeURIComponent(item.session_id)}`} className="staff-patient-link text-primary">
                          {item.urgent_help ? help("unknownPatient") : item.patient_display}
                        </Link>{item.urgent_help ? <span className="staff-urgent-reference" dir="ltr">{visitReference(item.session_id)}</span> : null}
                      </td>
                      <td className={CELL}>
                        {[
                          item.age_years === undefined ? null : t("age", { age: item.age_years }),
                          item.sex ? t(`sex.${item.sex}`) : null,
                        ]
                          .filter(Boolean)
                          .join(", ") || t("unknown")}
                      </td>
                      <td className={CELL}>{item.complaint ?? item.reception_context?.reason ?? t("unknown")}</td>
                      <td className={CELL}>{item.reception_context ? <ReceptionPreference context={item.reception_context} /> : <span className="text-ink-muted">{reception("notRecorded")}</span>}</td>
                      <td className={CELL}>
                        {item.urgent_help ? <span className="staff-urgent-badge">{help("badge")}</span> : null}
                        {item.alerts.map((alert) => (
                          <span
                            key={alert.alert_id}
                            data-rule-id={alert.rule_id}
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
                      <td className={CELL}><span className="staff-state-chip" data-state={item.state}>{t(`states.${item.state}`)}</span>{item.queue_token ? <p className="staff-assigned-queue">{reception("assignedQueue")}: <strong>{item.queue_token.department}</strong><span dir="ltr">{item.queue_token.token_code}</span></p> : null}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          </section>
        </div>
      ); }}
    </ApiStatus>
  );
}
