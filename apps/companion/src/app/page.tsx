import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { ClaimForm } from "@/components/ClaimForm";

export default async function ClaimPage() {
  const t = await getTranslations("claim");
  return (
    <>
      <h1 className="text-title font-bold">{t("title")}</h1>
      <p>{t("intro")}</p>
      <Suspense>
        <ClaimForm />
      </Suspense>
    </>
  );
}
