import { getTranslations } from "next-intl/server";

import { DoctorSessionsView } from "@/components/DoctorSessionsView";

export default async function DoctorPage() {
  const t = await getTranslations("doctor");
  return (
    <>
      <h1 className="text-display font-bold">{t("listTitle")}</h1>
      <DoctorSessionsView />
    </>
  );
}
