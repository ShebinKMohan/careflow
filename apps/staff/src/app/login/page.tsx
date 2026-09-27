import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { LoginForm } from "@/components/LoginForm";

export default async function LoginPage() {
  const t = await getTranslations("login");
  return (
    <div className="flex max-w-xl flex-col gap-6">
      <h1 className="text-display font-bold">{t("title")}</h1>
      <p>{t("intro")}</p>
      <Suspense>
        <LoginForm />
      </Suspense>
    </div>
  );
}
