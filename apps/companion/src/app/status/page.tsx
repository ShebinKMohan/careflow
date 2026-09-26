import { getTranslations } from "next-intl/server";

import { StatusView } from "@/components/StatusView";

export default async function StatusPage() {
  const t = await getTranslations("status");
  return (
    <>
      <h1 className="text-title font-bold">{t("title")}</h1>
      <StatusView />
    </>
  );
}
