"use client";

import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";

import { api } from "@/lib/api";
import { useApiData } from "@/lib/useApiData";

import { ApiStatus } from "./ApiStatus";

const CELL = "border-b border-border px-3 py-3 text-start";

/** GET /v1/doctor/sessions: today's confirmed sessions, each linking to its briefing. */
export function DoctorSessionsView() {
  const t = useTranslations("doctor");
  const tAlerts = useTranslations("alerts");
  const format = useFormatter();
  const state = useApiData(() => api.GET("/v1/doctor/sessions"), "doctor-sessions");
  return (
    <ApiStatus state={state}>
      {(sessions) =>
        sessions.length === 0 ? (
          <p>{t("none")}</p>
        ) : (
          <table className="w-full border-collapse bg-surface">
            <thead>
              <tr>
                <th className={CELL}>{t("token")}</th>
                <th className={CELL}>{t("arrived")}</th>
                <th className={CELL}>{t("level")}</th>
                <th className={CELL}>{t("alerts")}</th>
                <th className={CELL}>
                  <span className="sr-only">{t("open")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((session) => (
                <tr key={session.session_id}>
                  <td className={`${CELL} font-mono font-bold`} dir="ltr">
                    {session.token_code}
                  </td>
                  <td className={CELL}>{format.dateTime(new Date(session.arrived_at), { timeStyle: "short" })}</td>
                  <td className={CELL}>{t("levelValue", { scale: session.scale, level: session.confirmed_level })}</td>
                  <td className={CELL}>
                    <ul className="flex flex-wrap gap-2">
                      {session.alert_chips.map((chip) => (
                        <li
                          key={chip.alert_id}
                          className={`rounded-control border-2 px-2 text-label font-semibold ${
                            chip.severity === "critical"
                              ? "border-danger bg-danger text-on-danger"
                              : "border-danger text-danger"
                          }`}
                        >
                          {tAlerts(`severity.${chip.severity}`)}: {tAlerts(`rules.${chip.rule_id}`)}
                        </li>
                      ))}
                    </ul>
                  </td>
                  <td className={CELL}>
                    <Link
                      href={`/doctor/${encodeURIComponent(session.session_id)}`}
                      className="font-semibold text-primary underline"
                    >
                      {t("open")}
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      }
    </ApiStatus>
  );
}
