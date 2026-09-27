"use client";

import type { Schema } from "@careflow/contracts";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { api } from "@/lib/api";
import type { WorklistItem } from "@/lib/sortWorklist";

type QueueToken = Schema<"QueueToken">;
type Action = "arrived" | "no-show" | "close";

const BUTTON = "min-h-touch rounded-control border-2 border-primary px-4 font-semibold text-primary disabled:opacity-60";
const PRIMARY = "min-h-touch rounded-control bg-primary px-4 font-semibold text-on-primary disabled:opacity-60";

/**
 * The nurse's queue controls (M2-07): call the next patient of a department into the first free
 * room, then mark a called patient arrived, a no-show, or seen (close). Closing or a no-show frees
 * the room. The queue decides who is next (nurse-confirmed level, then time waited); the phone and
 * the board follow over the live channels. `onChanged` re-reads the worklist.
 */
export function QueueControls({ rows, onChanged }: { rows: WorklistItem[]; onChanged: () => void }) {
  const t = useTranslations("nurse.queue");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const tokens = rows.map((row) => row.queue_token).filter((token): token is QueueToken => token !== undefined);
  const waiting = [...new Set(tokens.filter((token) => token.status === "waiting").map((token) => token.department))].sort();
  const called = tokens.filter((token) => token.status === "called" || token.status === "arrived");
  if (!waiting.length && !called.length) return null;

  async function run(work: () => Promise<{ ok: boolean; text: string }>) {
    setBusy(true);
    try {
      const { ok, text } = await work();
      setMessage({ text, error: !ok });
      if (ok) onChanged();
    } catch {
      setMessage({ text: t("failed"), error: true });
    } finally {
      setBusy(false);
    }
  }

  const callNext = (department: string) =>
    run(async () => {
      const { data, response } = await api.POST("/v1/queue/{department}/call-next", { params: { path: { department } } });
      if (!data) return { ok: false, text: response.status === 409 ? t("noRoom") : t("failed") };
      if (!data.token) return { ok: true, text: t("nobody") };
      return { ok: true, text: t("called", { code: data.token.token_code, room: data.token.room ?? "" }) };
    });

  const act = (token: QueueToken, action: Action) =>
    run(async () => {
      const params = { params: { path: { token_id: token.token_id } } };
      const result =
        action === "arrived"
          ? await api.POST("/v1/queue/tokens/{token_id}/arrived", params)
          : action === "no-show"
            ? await api.POST("/v1/queue/tokens/{token_id}/no-show", params)
            : await api.POST("/v1/queue/tokens/{token_id}/close", params);
      return result.data ? { ok: true, text: t(`done.${action}`, { code: token.token_code }) } : { ok: false, text: t("failed") };
    });

  return (
    <section aria-label={t("title")} className="flex flex-col gap-3 rounded-card border-2 border-border bg-surface p-4" data-testid="queue-controls">
      <h2 className="text-title font-semibold">{t("title")}</h2>
      {waiting.length ? (
        <div className="flex flex-wrap gap-2">
          {waiting.map((department) => (
            <button key={department} type="button" className={PRIMARY} disabled={busy} onClick={() => void callNext(department)}>
              {t("callNext", { department: department.charAt(0).toUpperCase() + department.slice(1) })}
            </button>
          ))}
        </div>
      ) : null}
      {called.length ? (
        <ul className="flex flex-col gap-2">
          {called.map((token) => (
            <li key={token.token_id} className="flex flex-wrap items-center gap-2" data-testid="called-token">
              <span className="font-bold" dir="ltr">
                {token.token_code}
              </span>
              <span>{token.room ? t("inRoom", { room: token.room }) : null}</span>
              <span className="text-label text-ink-muted">{t(token.status === "called" ? "status.called" : "status.arrived")}</span>
              {token.status === "called" ? (
                <>
                  <button type="button" className={BUTTON} disabled={busy} onClick={() => void act(token, "arrived")}>
                    {t("arrived")}
                  </button>
                  <button type="button" className={BUTTON} disabled={busy} onClick={() => void act(token, "no-show")}>
                    {t("noShow")}
                  </button>
                </>
              ) : null}
              <button type="button" className={BUTTON} disabled={busy} onClick={() => void act(token, "close")}>
                {t("close")}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {message ? (
        <p role={message.error ? "alert" : "status"} className={message.error ? "font-semibold text-danger" : "font-semibold"}>
          {message.text}
        </p>
      ) : null}
    </section>
  );
}
