import { getTranslations } from "next-intl/server";

import { CheckinFlow } from "@/components/CheckinFlow";

export default async function CheckinPage() {
  const t = await getTranslations("checkin");
  return (
    <>
      <h1 className="text-display font-bold">{t("title")}</h1>
      <CheckinFlow />
    </>
  );
}
