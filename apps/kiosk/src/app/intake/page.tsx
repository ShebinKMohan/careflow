import { getTranslations } from "next-intl/server";

import { IntakeFlow } from "@/components/IntakeFlow";

export default async function IntakePage() {
  const t = await getTranslations("intake");
  return (
    <>
      <h1 className="text-display font-bold">{t("title")}</h1>
      <IntakeFlow />
    </>
  );
}
