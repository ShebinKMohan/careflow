"use client";

import { useTranslations } from "next-intl";

import { api } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";

import { ApiStatus } from "./ApiStatus";

export const BOARD_POLL_MS = 3_000;
const CALLED_SHOWN = 8;

/**
 * GET /v1/board/{department}, every 3 s: a public screen, so token numbers and rooms only (never a
 * name or a level). Only the three contract fields are ever read. Dark and high contrast.
 */
export function BoardView({ department }: { department: string }) {
  const t = useTranslations("board");
  const state = usePolling(
    () => api.GET("/v1/board/{department}", { params: { path: { department } } }),
    `board:${department}`,
    BOARD_POLL_MS,
  );
  return (
    <section
      aria-label={t("title", { department })}
      className="flex min-h-[70dvh] flex-col gap-8 rounded-card bg-board p-8 text-on-board"
      data-testid="board"
    >
      <ApiStatus state={state}>
        {(entries) => {
          const called = entries
            .filter((entry) => entry.status === "called")
            .slice(0, CALLED_SHOWN)
            .map(({ token_code, room }) => ({ token_code, room }));
          const waiting = entries.filter((entry) => entry.status === "waiting").length;
          return (
            <>
              <h2 className="text-title font-bold text-on-board-muted">{t("nowCalling")}</h2>
              {called.length === 0 ? (
                <p className="text-title">{t("noneCalled")}</p>
              ) : (
                <ul className="grid gap-6 sm:grid-cols-2">
                  {called.map((entry) => (
                    <li key={entry.token_code} className="flex flex-col rounded-card border-2 border-on-board p-4">
                      <span className="font-mono text-[4.5rem] font-bold leading-none" dir="ltr">
                        {entry.token_code}
                      </span>
                      <span className="text-title">{entry.room ?? t("noRoom")}</span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="text-title" aria-live="off">
                {t("waiting", { count: waiting })}
              </p>
            </>
          );
        }}
      </ApiStatus>
    </section>
  );
}
