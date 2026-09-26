import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { LoginForm } from "@/components/LoginForm";

export default async function LoginPage() {
  const t = await getTranslations("login");
  return (
    <>
      <h1 className="text-display font-bold">{t("title")}</h1>
      <p>{t("intro")}</p>
      <Suspense>
        <LoginForm />
      </Suspense>
    </>
  );
}
