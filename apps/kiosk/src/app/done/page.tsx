import { getTranslations } from "next-intl/server";

import { DoneScreen } from "@/components/DoneScreen";

export default async function DonePage() {
  const t = await getTranslations("done");
  return (
    <>
      <h1 className="text-display font-bold">{t("title")}</h1>
      <DoneScreen />
    </>
  );
}
