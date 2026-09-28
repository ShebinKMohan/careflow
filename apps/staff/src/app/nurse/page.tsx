import { getTranslations } from "next-intl/server";

import { Worklist } from "@/components/Worklist";

export default async function NursePage() {
  const t = await getTranslations("nurse");
  const workspace = await getTranslations("workspace");
  return (
    <>
      <div className="staff-page-heading">
        <p className="staff-eyebrow">{workspace("workspace")}</p>
      <h1 className="text-display font-bold">{t("title")}</h1>
        <p>{workspace("nurseDescription")}</p>
      </div>
      <Worklist />
    </>
  );
}
