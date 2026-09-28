import { getTranslations } from "next-intl/server";

import { AdminStatusView } from "@/components/AdminStatusView";

export default async function AdminStatusPage() {
  const t = await getTranslations("adminStatus");
  const workspace = await getTranslations("workspace");
  return (
    <>
      <div className="staff-page-heading">
        <p className="staff-eyebrow">{workspace("workspace")}</p>
      <h1 className="text-display font-bold">{t("title")}</h1>
        <p>{workspace("adminDescription")}</p>
      </div>
      <AdminStatusView />
    </>
  );
}
