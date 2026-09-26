import { getTranslations } from "next-intl/server";

import { ProvidersView } from "@/components/ProvidersView";

export default async function AdminPage() {
  const t = await getTranslations("admin");
  return (
    <>
      <h1 className="text-display font-bold">{t("title")}</h1>
      <ProvidersView />
    </>
  );
}
