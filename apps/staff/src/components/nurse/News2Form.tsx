"use client";

import type { Schema } from "@careflow/contracts";
import { Card } from "@careflow/ui";
import { useTranslations } from "next-intl";
import { type FormEvent, useState } from "react";

import { api } from "@/lib/api";

type News2Score = Schema<"News2Score">;
type TriageView = Schema<"TriageView">;
type Acvpu = Schema<"AcvpuCode">;

const ACVPU: readonly Acvpu[] = ["A", "C", "V", "P", "U"];
const PARAMETERS = [
  "respiratory_rate",
  "spo2",
  "systolic_bp",
  "heart_rate",
  "acvpu",
  "temperature_c",
  "on_supplemental_o2",
] as const;
const FIELD = "min-h-touch w-40 rounded-control border-2 border-border bg-surface px-4 text-body";
const TOGGLE = "min-h-touch min-w-touch rounded-control border-2 border-border px-4 font-semibold";

/** The NEWS2 as scored: the total, its band and every subscore; "Partial" until all 7 are in. */
export function News2Summary({ news2 }: { news2: News2Score | null | undefined }) {
  const t = useTranslations("news2");
  if (!news2) return <p>{t("none")}</p>;
  return (
    <div className="flex flex-col gap-3" data-testid="news2-summary">
      <p className="flex flex-wrap items-baseline gap-3">
        <span className="text-title font-bold">{t("score", { total: news2.total })}</span>
        <span className="font-semibold">{t(`band.${news2.risk_band}`)}</span>
        {news2.partial ? (
          <span className="rounded-control border-2 border-danger px-2 font-semibold text-danger" data-testid="news2-partial">
            {t("partial", { missing: news2.missing.length })}
          </span>
        ) : null}
      </p>
      <dl className="grid grid-cols-1 gap-x-8 gap-y-1 sm:grid-cols-2">
        {PARAMETERS.map((parameter) => (
          <div key={parameter} className="flex justify-between gap-4 border-b border-border py-1">
            <dt>{t(`params.${parameter}`)}</dt>
            <dd className="font-semibold">{news2.subscores[parameter] ?? t("missing")}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/**
 * The nurse's four NEWS2 inputs: respiratory rate, systolic BP (pre-filled from a device reading,
 * marked to confirm), ACVPU and supplemental oxygen. `POST /v1/nurse/sessions/{id}/vitals`
 * re-runs the full NEWS2 and the rules; the returned triage view replaces the page's.
 */
export function News2Form({
  sessionId,
  news2,
  deviceSystolic,
  onSaved,
}: {
  sessionId: string;
  news2: News2Score | null | undefined;
  deviceSystolic?: number;
  onSaved: (view: TriageView) => void;
}) {
  const t = useTranslations("news2");
  const [respiratoryRate, setRespiratoryRate] = useState("");
  const [systolic, setSystolic] = useState(deviceSystolic === undefined ? "" : String(deviceSystolic));
  const [acvpu, setAcvpu] = useState<Acvpu | null>(null);
  const [oxygen, setOxygen] = useState<boolean | null>(null);
  const [status, setStatus] = useState<"idle" | "saving" | "failed">("idle");

  const body: Schema<"NurseVitalsRequest"> = {
    ...(respiratoryRate.trim() ? { respiratory_rate: Number(respiratoryRate) } : {}),
    ...(systolic.trim() ? { systolic_bp: Number(systolic) } : {}),
    ...(acvpu ? { acvpu } : {}),
    ...(oxygen === null ? {} : { on_supplemental_o2: oxygen }),
  };
  const valid = Object.keys(body).length > 0 && Object.values(body).every((v) => typeof v !== "number" || (Number.isFinite(v) && v >= 0));

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!valid || status === "saving") return;
    setStatus("saving");
    try {
      const { data } = await api.POST("/v1/nurse/sessions/{session_id}/vitals", {
        params: { path: { session_id: sessionId } },
        body,
      });
      if (!data) {
        setStatus("failed");
        return;
      }
      setStatus("idle");
      onSaved(data);
    } catch {
      setStatus("failed");
    }
  }

  return (
    <Card title={t("title")}>
      <div className="mb-6">
        <News2Summary news2={news2} />
      </div>
      <form className="flex flex-col gap-4" onSubmit={submit} noValidate>
        <div className="flex flex-wrap gap-6">
          <label className="flex flex-col gap-2 font-semibold">
            {t("rr")}
            <input
              inputMode="numeric"
              value={respiratoryRate}
              onChange={(event) => setRespiratoryRate(event.target.value)}
              className={FIELD}
            />
          </label>
          <label className="flex flex-col gap-2 font-semibold">
            {t("sbp")}
            <input
              inputMode="numeric"
              value={systolic}
              onChange={(event) => setSystolic(event.target.value)}
              className={FIELD}
              aria-describedby={deviceSystolic === undefined ? undefined : "sbp-device"}
            />
            {deviceSystolic === undefined ? null : (
              <span id="sbp-device" className="text-label font-normal">
                {t("fromDevice")}
              </span>
            )}
          </label>
        </div>
        <fieldset className="flex flex-col gap-2">
          <legend className="font-semibold">{t("acvpu")}</legend>
          <div className="flex flex-wrap gap-2">
            {ACVPU.map((code) => (
              <button
                key={code}
                type="button"
                aria-pressed={acvpu === code}
                aria-label={t(`acvpuOptions.${code}`)}
                className={`${TOGGLE} ${acvpu === code ? "bg-primary text-on-primary" : "bg-surface"}`}
                onClick={() => setAcvpu(code)}
              >
                {code}
              </button>
            ))}
          </div>
        </fieldset>
        <fieldset className="flex flex-col gap-2">
          <legend className="font-semibold">{t("oxygen")}</legend>
          <div className="flex gap-2">
            {([true, false] as const).map((value) => (
              <button
                key={String(value)}
                type="button"
                aria-pressed={oxygen === value}
                className={`${TOGGLE} ${oxygen === value ? "bg-primary text-on-primary" : "bg-surface"}`}
                onClick={() => setOxygen(value)}
              >
                {value ? t("yes") : t("no")}
              </button>
            ))}
          </div>
        </fieldset>
        <button
          type="submit"
          className="min-h-touch w-fit rounded-control bg-primary px-6 font-semibold text-on-primary disabled:opacity-60"
          disabled={!valid || status === "saving"}
        >
          {status === "saving" ? t("saving") : t("save")}
        </button>
        {status === "failed" ? (
          <p role="alert" className="font-semibold text-danger">
            {t("failed")}
          </p>
        ) : null}
      </form>
    </Card>
  );
}
