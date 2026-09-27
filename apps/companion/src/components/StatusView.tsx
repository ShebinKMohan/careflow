"use client";

import { Card } from "@careflow/ui";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { api } from "@/lib/api";
import { useApiData } from "@/lib/useApiData";

/** GET /v1/companion/status: queue position and number only; never a level or an alert. */
export function StatusView() {
  const t = useTranslations("status");
  const state = useApiData(() => api.GET("/v1/companion/status"), "status");
  if (state.status === "loading") return <p aria-busy="true">{t("loading")}</p>;
  if (state.status === "failed") {
    return (
      <p role="alert" className="font-semibold text-danger">
        {t("failed")}
      </p>
    );
  }
  if (state.status === "unauthorized") {
    return (
      <Card>
        <p className="mb-4">{t("unauthorized")}</p>
        <Link href="/" className="inline-flex min-h-touch items-center font-semibold text-primary underline">
          {t("claimLink")}
        </Link>
      </Card>
    );
  }
  const { position, wait_estimate_minutes: minutes, token } = state.data;
  return (
    <Card>
      <p className="text-display font-bold" dir="ltr">
        {token ? token.token_code : null}
      </p>
      <p>{token ? t.rich("token", { code: token.token_code, ltr: (chunks) => <bdi dir="ltr">{chunks}</bdi> }) : t("noToken")}</p>
      <p>{t("position", { position })}</p>
      <p>{t("wait", { minutes })}</p>
    </Card>
  );
}
