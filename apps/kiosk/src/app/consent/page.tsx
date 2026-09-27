import { getTranslations } from "next-intl/server";

import { ConsentScreen } from "@/components/ConsentScreen";

export default async function ConsentPage() {
  const t = await getTranslations("safety.consent");
  return (
    <>
      <h1 className="text-display font-bold">{t("title")}</h1>
      <ConsentScreen />
    </>
  );
}
