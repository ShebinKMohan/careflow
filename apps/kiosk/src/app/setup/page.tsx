import { getTranslations } from "next-intl/server";

import { DeviceSetupForm } from "@/components/DeviceSetupForm";

export default async function SetupPage() {
  const t = await getTranslations("setup");
  return (
    <>
      <h1 className="text-display font-bold">{t("title")}</h1>
      <p>{t("intro")}</p>
      <DeviceSetupForm />
    </>
  );
}
