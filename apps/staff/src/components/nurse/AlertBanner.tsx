"use client";

import Link from "next/link";
import { useFormatter, useLocale, useTranslations } from "next-intl";

import { type Alert, alertText, ECG_RULE, ECG_TASK } from "@/lib/alerts";
import { api } from "@/lib/api";
import type { FactView } from "@/lib/facts";
import { factLabel } from "@/lib/factLabel";
import type { Task } from "@/lib/useSessionAlerts";

import { EcgCountdown } from "./EcgCountdown";
import { ProvenanceChip } from "./ProvenanceChip";

/**
 * One active alert, above everything else (safety rule 1). Silent and visual only: no sound, and
 * no live-region announcement, so nothing reaches the lobby. It shows the rule, the fixed alert
 * line, the evidence facts with their provenance, when it was raised and whether NEWS2 was partial.
 */
export function AlertBanner({
  alert,
  facts,
  tasks,
  patient,
  onTaskDone,
}: {
  alert: Alert;
  facts: Map<string, FactView>;
  tasks: Task[];
  /** The patient's initials, on the worklist (the triage card already names the patient). */
  patient?: string;
  onTaskDone?: (task: Task) => void;
}) {
  const t = useTranslations("alerts");
  const format = useFormatter();
  const locale = useLocale() === "ar" ? "ar" : "en";
  const evidence = alert.evidence_fact_ids.map((id) => facts.get(id)).filter((fact) => fact !== undefined);
  const ecgTask = tasks.find((task) => task.kind === ECG_TASK);
  const critical = alert.severity === "critical";

  async function completeTask(task: Task): Promise<boolean> {
    const { data } = await api.POST("/v1/nurse/sessions/{session_id}/tasks/{task_id}/complete", {
      params: { path: { session_id: task.session_id, task_id: task.task_id } },
    });
    if (data) onTaskDone?.(data);
    return Boolean(data);
  }

  return (
    <section
      aria-label={t(`rules.${alert.rule_id}`)}
      data-testid="alert-banner"
      className={`flex flex-col gap-3 rounded-card border-4 p-gutter ${
        critical ? "border-danger bg-danger text-on-danger" : "border-danger bg-surface text-ink"
      }`}
    >
      <div className="flex flex-wrap items-baseline gap-3">
        <span className="rounded-control border-2 border-current px-2 font-bold uppercase">
          {t(`severity.${alert.severity}`)}
        </span>
        <h2 className="text-title font-bold">{t(`rules.${alert.rule_id}`)}</h2>
        {patient ? <span className="text-body font-semibold">{t("patient", { name: patient })}</span> : null}
        {alert.partial ? (
          <span className="rounded-control border-2 border-current px-2 font-semibold">{t("partial")}</span>
        ) : null}
      </div>
      <p className="text-body font-semibold">{alertText(alert, locale)}</p>
      <div>
        <h3 className="font-semibold">{t("evidence")}</h3>
        {evidence.length ? (
          <ul className="flex flex-wrap gap-3" data-testid="alert-evidence">
            {evidence.map((fact) => (
              <li key={fact.factId} className="flex items-center gap-2 rounded-control bg-surface px-3 py-1 text-ink">
                <span>
                  {factLabel(fact.path)}: <strong>{fact.value}</strong>
                </span>
                <ProvenanceChip provenance={fact.provenance} />
              </li>
            ))}
          </ul>
        ) : (
          <p>{t("evidenceLoading")}</p>
        )}
      </div>
      <p className="text-label">{t("raised", { time: format.dateTime(new Date(alert.raised_at), { timeStyle: "short" }) })}</p>
      {alert.rule_id === ECG_RULE && alert.deadline_at ? (
        // On a light panel, so the red of an urgent countdown never sits on the red banner.
        <div className="rounded-control bg-surface p-3 text-ink">
          <EcgCountdown deadline={alert.deadline_at} task={ecgTask} onDone={onTaskDone ? completeTask : undefined} />
        </div>
      ) : null}
      {patient ? (
        <Link
          href={`/nurse/${encodeURIComponent(alert.session_id)}`}
          className="inline-flex min-h-touch w-fit items-center rounded-control bg-surface px-6 font-semibold text-ink underline"
        >
          {t("openCard")}
        </Link>
      ) : null}
    </section>
  );
}
