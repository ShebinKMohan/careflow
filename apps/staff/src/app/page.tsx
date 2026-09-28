import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { WorkspaceIcon } from "@/components/SiteHeader";

const WORKSPACES = [
  { href: "/nurse", key: "nurse", icon: "pulse", number: "01" },
  { href: "/doctor", key: "doctor", icon: "file", number: "02" },
  { href: "/board?department=ed", key: "board", icon: "board", number: "03" },
  { href: "/admin/status", key: "admin", icon: "settings", number: "04" },
] as const;

export default async function StaffHome() {
  const t = await getTranslations("workspace");
  return (
    <div className="staff-home">
      <section className="staff-home-hero">
        <p className="staff-eyebrow">{t("eyebrow")}</p>
        <h1>{t("welcome")}</h1>
        <p className="staff-home-lead">{t("welcomeNote")}</p>
        <div className="staff-hero-path" aria-hidden="true"><span /><i /><span /><i /><span /></div>
      </section>
      <section aria-labelledby="workspace-heading">
        <h2 id="workspace-heading" className="staff-section-title">{t("roleLabel")}</h2>
        <div className="staff-role-grid">
          {WORKSPACES.map(({ href, key, icon, number }) => (
            <Link key={key} href={href} className="staff-role-card">
              <div className="staff-role-top"><span className="staff-role-icon"><WorkspaceIcon name={icon} /></span><span>{number}</span></div>
              <h3>{t(key)}</h3>
              <p>{t(`${key}Note`)}</p>
              <span className="staff-role-action">{t("openWorkspace")}<span aria-hidden="true">↗</span></span>
            </Link>
          ))}
        </div>
      </section>
      <aside className="staff-principle"><span aria-hidden="true">+</span><div><strong>{t("principle")}</strong><p>{t("principleNote")}</p></div></aside>
    </div>
  );
}
