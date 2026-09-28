"use client";

import { Card } from "@careflow/ui";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import type { ApiState } from "@/lib/useApiData";

/** Loading, sign-in and error states around a loaded view. */
export function ApiStatus<T>({ state, children }: { state: ApiState<T>; children: (data: T) => ReactNode }) {
  const t = useTranslations("common");
  const pathname = usePathname();
  if (state.status === "ready") return <>{children(state.data)}</>;
  if (state.status === "loading") return <div className="staff-loading" role="status" aria-busy="true"><span className="staff-loading-dot" aria-hidden="true" /><p>{t("loading")}</p></div>;
  if (state.status === "unauthorized") {
    return (
      <Card>
        <p className="mb-4">{t("unauthorized")}</p>
        <Link
          href={`/login?next=${encodeURIComponent(pathname)}`}
          className="inline-flex min-h-touch items-center rounded-control bg-primary px-6 font-semibold text-on-primary"
        >
          {t("signIn")}
        </Link>
      </Card>
    );
  }
  return (
    <p role="alert" className="staff-error font-semibold text-danger">
      {t("failed")}
    </p>
  );
}
