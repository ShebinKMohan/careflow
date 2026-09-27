import { getTranslations } from "next-intl/server";

import { ClaimFromLink } from "@/components/ClaimFromLink";

export default async function ClaimLinkPage() {
  const t = await getTranslations("claim");
  return (
    <>
      <h1 className="text-title font-bold">{t("title")}</h1>
      <ClaimFromLink />
    </>
  );
}
