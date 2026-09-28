import { getTranslations } from "next-intl/server";

import { WaitingRoom } from "@/components/WaitingRoom";

export default async function StatusPage() {
  const t = await getTranslations("status");
  return (
    <>
      <div className="companion-page-heading">
        <span className="companion-eyebrow">{t("eyebrow")}</span>
        <h1>{t("title")}</h1>
        <p>{t("intro")}</p>
      </div>
      <WaitingRoom />
    </>
  );
}
