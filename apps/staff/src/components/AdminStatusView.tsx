"use client";

import type { Schema } from "@careflow/contracts";
import { Card } from "@careflow/ui";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { api } from "@/lib/api";
import { useApiData } from "@/lib/useApiData";

import { ApiStatus } from "./ApiStatus";

type ServiceStatus = Schema<"ServiceStatus">;

const CELL = "border-b border-border px-3 py-3 text-start";

/**
 * GET /v1/admin/status and /v1/admin/providers: which implementation runs every intake port (a
 * "stub" badge where a stand-in is in use), which provider and model serve every AI task, the
 * services' health and the ruleset and urgency-model versions. Refresh re-reads both.
 */
export function AdminStatusView() {
  const t = useTranslations("adminStatus");
  const [round, setRound] = useState(0);
  const status = useApiData(() => api.GET("/v1/admin/status"), `admin-status:${round}`);
  const providers = useApiData(() => api.GET("/v1/admin/providers"), `admin-providers:${round}`);
  return (
    <div className="flex flex-col gap-6">
      <button
        type="button"
        onClick={() => setRound((value) => value + 1)}
        className="inline-flex min-h-touch w-fit items-center rounded-control bg-primary px-6 font-semibold text-on-primary"
      >
        {t("refresh")}
      </button>
      <ApiStatus state={status}>
        {(body) => {
          const byPrefix = (prefix: string) => body.services.filter((s) => s.name.startsWith(prefix));
          const infra = body.services.filter((s) => !s.name.includes(":") && s.name !== "urgency-model");
          const model = body.services.find((s) => s.name === "urgency-model");
          return (
            <>
              <Card title={t("versions")}>
                <dl className="staff-admin-versions">
                  <div><dt>{t("api")}</dt><dd className="font-mono">{body.api_version}</dd></div>
                  <div><dt>{t("ruleset")}</dt><dd className="font-mono">{body.ruleset_version}</dd></div>
                  <div><dt>{t("urgencyModel")}</dt><dd className="font-mono">{model?.detail ?? t("unknown")}</dd></div>
                </dl>
              </Card>
              <div className="staff-admin-grid">
              <StatusTable title={t("services")} rows={infra} />
              <StatusTable title={t("ports")} rows={byPrefix("port:")} stubs />
              <StatusTable title={t("aiTasks")} rows={byPrefix("ai:")} />
              <StatusTable title={t("speech")} rows={byPrefix("speech:")} />
              </div>
            </>
          );
        }}
      </ApiStatus>
      <ApiStatus state={providers}>
        {({ tasks }) => (
          <Card title={t("providers")} className="staff-admin-providers">
            <table className="w-full border-collapse">
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
                  <tr key={task.task} data-task={task.task}>
                    <td className={`${CELL} font-mono`}>{task.task}</td>
                    <td className={CELL}>{task.provider}</td>
                    <td className={`${CELL} font-mono`}>{task.model ?? task.tool ?? ""}</td>
                    <td className={CELL}>
                      {task.available ? t("yes") : t("no")}
                      {task.detail ? <span className="ms-2 text-label text-ink-muted">{task.detail}</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}
      </ApiStatus>
    </div>
  );
}

function StatusTable({ title, rows, stubs = false }: { title: string; rows: ServiceStatus[]; stubs?: boolean }) {
  const t = useTranslations("adminStatus");
  return (
    <Card title={title}>
      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className={CELL}>{t("name")}</th>
            <th className={CELL}>{t("status")}</th>
            <th className={CELL}>{t("detail")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.name} data-row={row.name}>
              <td className={`${CELL} font-mono`}>{row.name.replace(/^(port|ai|speech):/, "")}</td>
              <td className={CELL}>
                <span className={row.status === "ok" ? "text-success" : "font-semibold text-danger"}>
                  {t(`state.${row.status}`)}
                </span>
                {stubs && row.status !== "ok" ? (
                  <span className="ms-2 rounded-control border-2 border-danger px-2 text-label font-semibold text-danger">
                    {t("stub")}
                  </span>
                ) : null}
              </td>
              <td className={CELL}>{row.detail ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
