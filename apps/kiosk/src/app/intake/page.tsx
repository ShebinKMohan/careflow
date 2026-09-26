import { Card } from "@careflow/ui";
import { getTranslations } from "next-intl/server";

export default async function IntakePage() {
  const t = await getTranslations("intake");
  return (
    <>
      <h1 className="text-display font-bold">{t("title")}</h1>
      <Card>
        <p>{t("placeholder")}</p>
      </Card>
    </>
  );
}
