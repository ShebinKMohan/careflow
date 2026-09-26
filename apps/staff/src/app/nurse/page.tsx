import { getTranslations } from "next-intl/server";

import { Worklist } from "@/components/Worklist";

export default async function NursePage() {
  const t = await getTranslations("nurse");
  return (
    <>
      <h1 className="text-display font-bold">{t("title")}</h1>
      <Worklist />
    </>
  );
}
