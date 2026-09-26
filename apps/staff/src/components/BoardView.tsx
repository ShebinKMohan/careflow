"use client";

import { useTranslations } from "next-intl";

import { api } from "@/lib/api";
import { useApiData } from "@/lib/useApiData";

import { ApiStatus } from "./ApiStatus";

const CELL = "border-b border-border px-3 py-3 text-start text-title";

/** GET /v1/board/{department}: numbers and rooms only (a public screen). */
export function BoardView({ department }: { department: string }) {
  const t = useTranslations("board");
  const state = useApiData(
    () => api.GET("/v1/board/{department}", { params: { path: { department } } }),
    `board:${department}`,
  );
  return (
    <ApiStatus state={state}>
      {(entries) => (
        <table className="w-full border-collapse bg-surface">
          <thead>
            <tr>
              <th className={CELL}>{t("token")}</th>
              <th className={CELL}>{t("room")}</th>
              <th className={CELL}>{t("status")}</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.token_code}>
                <td className={`${CELL} font-mono font-bold`} dir="ltr">
                  {entry.token_code}
                </td>
                <td className={CELL}>{entry.room ?? t("noRoom")}</td>
                <td className={CELL}>{entry.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </ApiStatus>
  );
}
