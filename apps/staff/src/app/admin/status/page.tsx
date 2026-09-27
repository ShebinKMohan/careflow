import { getTranslations } from "next-intl/server";

import { AdminStatusView } from "@/components/AdminStatusView";

export default async function AdminStatusPage() {
  const t = await getTranslations("adminStatus");
  return (
    <>
      <h1 className="text-display font-bold">{t("title")}</h1>
      <AdminStatusView />
    </>
  );
}
