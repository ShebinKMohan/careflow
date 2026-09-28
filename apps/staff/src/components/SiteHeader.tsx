"use client";

import { LanguageToggle } from "@careflow/ui";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { useSyncExternalStore } from "react";

import { clearToken, readToken, subscribeToken } from "@/lib/session";

const NAV = [
  { href: "/", key: "overview", icon: "grid" },
  { href: "/nurse", key: "nurse", icon: "pulse" },
  { href: "/doctor", key: "doctor", icon: "file" },
  { href: "/board?department=ed", key: "board", icon: "board" },
  { href: "/admin/status", key: "admin", icon: "settings" },
] as const;

export function WorkspaceIcon({ name }: { name: string }) {
  const paths: Record<string, string> = {
    grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
    pulse: "M3 12h4l3-8 4 16 3-8h4",
    file: "M14 3H5v18h14V8z M14 3v5h5 M8 12h8 M8 16h6",
    board: "M3 4h18v14H3z M8 22h8 M12 18v4 M7 8h3 M7 12h3 M14 8h3 M14 12h3",
    settings: "M4 6h16 M4 12h16 M4 18h16 M8 3v6 M16 9v6 M10 15v6",
  };
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name] ?? paths.grid} /></svg>;
}

export function SiteHeader() {
  const t = useTranslations("workspace");
  const locale = useLocale();
  const router = useRouter();
  const pathname = usePathname();
  const token = useSyncExternalStore(subscribeToken, readToken, () => null);
  return (
    <header className="staff-sidebar" data-screen={pathname === "/board" ? "board" : "workspace"}>
      <Link href="/" className="staff-brand" aria-label={t("home")}>
        <span className="staff-brand-mark" aria-hidden="true">+</span>
        <span><span className="staff-brand-name">{t("hospitalBrand")}</span><span className="staff-brand-hospital">{t("hospitalKind")}</span><span className="staff-brand-caption">CareFlow · {t("staffWorkspace")}</span></span>
      </Link>
      <div className="staff-sidebar-section">{t("workspace")}</div>
      <nav aria-label={t("navigation")} className="staff-navigation">
        {NAV.map(({ href, key, icon }) => {
          const target = href.split("?")[0] ?? "/";
          const active = target === "/" ? pathname === "/" : pathname.startsWith(target === "/admin/status" ? "/admin" : target);
          return <Link key={href} href={href} className="staff-nav-link" aria-current={active ? "page" : undefined}><WorkspaceIcon name={icon} /><span>{t(key)}</span>{active ? <span className="staff-nav-dot" aria-hidden="true" /> : null}</Link>;
        })}
      </nav>
      <div className="staff-sidebar-bottom">
        <div className="staff-environment"><span aria-hidden="true" />{t("synthetic")}</div>
        <LanguageToggle locale={locale} onChange={() => router.refresh()} />
        {token ? <button type="button" className="staff-signin" onClick={() => { clearToken(); router.push("/login"); router.refresh(); }}>{t("signOut")}<span aria-hidden="true">↗</span></button> : <Link href="/login" className="staff-signin">{t("signIn")}<span aria-hidden="true">↗</span></Link>}
      </div>
    </header>
  );
}
