"use client";

import { useTranslations } from "next-intl";

import { api } from "@/lib/api";
import { useApiData } from "@/lib/useApiData";

import { ApiStatus } from "./ApiStatus";

const CELL = "border-b border-border px-3 py-3 text-start";

/** GET /v1/admin/providers: which model provider serves each AI task. */
export function ProvidersView() {
  const t = useTranslations("admin");
  const state = useApiData(() => api.GET("/v1/admin/providers"), "providers");
  return (
    <ApiStatus state={state}>
      {({ tasks }) => (
        <table className="w-full border-collapse bg-surface">
          <thead>
            <tr>
              <th className={CELL}>{t("task")}</th>
              <th className={CELL}>{t("provider")}</th>
              <th className={CELL}>{t("model")}</th>
              <th className={CELL}>{t("available")}</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task) => (
              <tr key={task.task}>
                <td className={`${CELL} font-mono`}>{task.task}</td>
                <td className={CELL}>{task.provider}</td>
                <td className={`${CELL} font-mono`}>{task.model ?? task.tool ?? ""}</td>
                <td className={CELL}>{task.available ? t("yes") : t("no")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </ApiStatus>
  );
}
