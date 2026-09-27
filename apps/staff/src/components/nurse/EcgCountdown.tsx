"use client";

import { useFormatter, useTranslations } from "next-intl";
import { useState } from "react";

import { countdown } from "@/lib/countdown";
import type { Task } from "@/lib/useSessionAlerts";
import { useNow } from "@/lib/useNow";

/**
 * The 10-minute ECG countdown: mm:ss to the deadline (red under 2 minutes), then "Overdue +mm:ss".
 * "ECG done" completes the task once; the countdown then stops at "ECG done hh:mm".
 */
export function EcgCountdown({
  deadline,
  task,
  onDone,
}: {
  deadline: string;
  task?: Task;
  onDone?: (task: Task) => Promise<boolean>;
}) {
  const t = useTranslations("alerts");
  const format = useFormatter();
  const now = useNow();
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);

  if (task?.status === "completed") {
    const at = task.completed_at ? format.dateTime(new Date(task.completed_at), { timeStyle: "short" }) : "";
    return (
      <p className="text-title font-bold" data-testid="ecg-status">
        {t("ecgDoneAt", { time: at })}
      </p>
    );
  }

  const left = countdown(Date.parse(deadline), now);
  async function done() {
    if (!task || !onDone || pending) return;
    setPending(true);
    setFailed(false);
    const ok = await onDone(task);
    setPending(false);
    if (!ok) setFailed(true);
  }

  return (
    <div className="flex flex-wrap items-center gap-4">
      <p className={`text-title font-bold ${left.urgent ? "text-danger" : ""}`} data-testid="ecg-status" aria-live="off">
        {left.overdue ? t("overdue", { time: left.time }) : `${t("ecgDue")} ${left.time}`}
      </p>
      {task && onDone ? (
        <button
          type="button"
          className="min-h-touch rounded-control bg-primary px-6 font-semibold text-on-primary disabled:opacity-60"
          disabled={pending}
          onClick={() => void done()}
        >
          {t("ecgDone")}
        </button>
      ) : null}
      {failed ? (
        <p role="alert" className="font-semibold text-danger">
          {t("ecgFailed")}
        </p>
      ) : null}
    </div>
  );
}
