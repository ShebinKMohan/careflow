import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { LoginForm } from "@/components/LoginForm";

export default async function LoginPage() {
  const t = await getTranslations("login");
  const workspace = await getTranslations("workspace");
  return (
    <div className="staff-login-layout">
      <section className="staff-login-story">
        <p className="staff-eyebrow">{workspace("loginEyebrow")}</p>
        <h1>{workspace("loginHeading")}</h1>
        <p>{workspace("loginNote")}</p>
        <div className="staff-login-art" aria-hidden="true"><span>+</span><i /><i /><i /></div>
        <small>{workspace("loginSafety")}</small>
      </section>
      <section className="staff-login-form">
        <span className="staff-brand-mark" aria-hidden="true">+</span>
        <h2>{t("title")}</h2>
        <p>{workspace("loginHelp")}</p>
        <Suspense><LoginForm /></Suspense>
      </section>
    </div>
  );
}
