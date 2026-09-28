import { getTranslations } from "next-intl/server";
import Link from "next/link";
import { CheckinFlow } from "@/components/CheckinFlow";

/** Explicit legacy demo-code entry, separate from the primary reception registration. */
export default async function ExistingCodePage() {
  const t = await getTranslations("checkin");
  const reception = await getTranslations("reception");
  return <><h1 className="text-display font-bold">{t("title")}</h1><Link href="/checkin" className="reception-secondary">{reception("back")}</Link><CheckinFlow /></>;
}
