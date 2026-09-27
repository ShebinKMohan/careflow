import { getTranslations } from "next-intl/server";

import { PatientChoice } from "@/components/PatientChoice";

export default async function PatientPage() {
  const t = await getTranslations("safety.child");
  return (
    <>
      <h1 className="text-display font-bold">{t("title")}</h1>
      <PatientChoice />
    </>
  );
}
