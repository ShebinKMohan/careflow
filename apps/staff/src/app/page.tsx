import { Card } from "@careflow/ui";
import Link from "next/link";
import { getTranslations } from "next-intl/server";

const LINKS = [
  { href: "/nurse", key: "nurse" },
  { href: "/doctor/ses_chest_pain", key: "doctor" },
  { href: "/board?department=ed", key: "board" },
  { href: "/admin", key: "admin" },
  { href: "/login", key: "login" },
] as const;

export default async function StaffHome() {
  const t = await getTranslations("home");
  return (
    <>
      <h1 className="text-display font-bold">{t("title")}</h1>
      <Card>
        <ul className="flex flex-col gap-3">
          {LINKS.map((link) => (
            <li key={link.href}>
              <Link href={link.href} className="inline-flex min-h-touch items-center font-semibold text-primary underline">
                {t(link.key)}
              </Link>
            </li>
          ))}
        </ul>
      </Card>
    </>
  );
}
