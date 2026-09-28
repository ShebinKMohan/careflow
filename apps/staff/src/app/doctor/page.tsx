import { getTranslations } from "next-intl/server";

import { DoctorSessionsView } from "@/components/DoctorSessionsView";

export default async function DoctorPage() {
  const t = await getTranslations("doctor");
  const workspace = await getTranslations("workspace");
  return (
    <>
      <div className="staff-page-heading">
        <p className="staff-eyebrow">{workspace("workspace")}</p>
      <h1 className="text-display font-bold">{t("listTitle")}</h1>
        <p>{workspace("doctorDescription")}</p>
      </div>
      <DoctorSessionsView />
    </>
  );
}
