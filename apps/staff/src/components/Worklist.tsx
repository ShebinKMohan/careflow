"use client";

import { useFormatter, useTranslations } from "next-intl";

import { api } from "@/lib/api";
import { useApiData } from "@/lib/useApiData";

import { ApiStatus } from "./ApiStatus";

const CELL = "border-b border-border px-3 py-3 text-start align-top";

/** GET /v1/nurse/worklist. The level shown is a suggestion; the nurse confirms every level. */
export function Worklist() {
  const t = useTranslations("nurse");
  const format = useFormatter();
  const state = useApiData(() => api.GET("/v1/nurse/worklist"), "worklist");
  return (
    <ApiStatus state={state}>
      {(items) => (
        <table className="w-full border-collapse bg-surface">
          <thead>
            <tr>
              <th className={CELL}>{t("patient")}</th>
              <th className={CELL}>{t("arrived")}</th>
              <th className={CELL}>{t("state")}</th>
              <th className={CELL}>{t("suggested")}</th>
              <th className={CELL}>{t("alerts")}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.session_id}>
                <td className={`${CELL} font-semibold`}>{item.patient_display}</td>
                <td className={CELL}>{format.dateTime(new Date(item.arrived_at), { timeStyle: "short" })}</td>
                <td className={CELL}>{item.state.replaceAll("_", " ")}</td>
                <td className={CELL}>
                  {item.suggested_level === undefined
                    ? t("noLevel")
                    : t("level", { scale: item.scale ?? "", level: item.suggested_level })}
                </td>
                <td className={CELL}>
                  {item.alerts.map((alert) => (
                    <span key={alert.alert_id} className="me-2 inline-block rounded-control bg-danger px-2 text-label text-on-danger">
                      {alert.rule_id}
                    </span>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </ApiStatus>
  );
}
