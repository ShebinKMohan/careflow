import { getTranslations } from "next-intl/server";

import { LanguageChoice } from "@/components/LanguageChoice";
import { StartFresh } from "@/components/StartFresh";

export default async function HomePage() {
  const t = await getTranslations("home");
  return (
    <>
      <StartFresh />
      <h1 className="text-display font-bold">{t("title")}</h1>
      <p>{t("subtitle")}</p>
      <LanguageChoice />
    </>
  );
}
