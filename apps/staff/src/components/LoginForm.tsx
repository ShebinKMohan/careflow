"use client";

import { BigButton } from "@careflow/ui";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { type FormEvent, useState } from "react";
import { z } from "zod";

import { api } from "@/lib/api";
import { saveToken } from "@/lib/session";

const Login = z.object({
  username: z.string().trim().min(1).max(64),
  password: z.string().min(1).max(256),
});

const FIELD = "min-h-touch w-full rounded-control border-2 border-border bg-surface px-4 text-body";

/** Only same-site paths, never another origin. */
function safeNext(value: string | null): string {
  return value && value.startsWith("/") && !value.startsWith("//") ? value : "/nurse";
}

/** Stub staff sign-in (POST /v1/auth/login); the token stays in this tab's session storage. */
export function LoginForm() {
  const t = useTranslations("login");
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const [status, setStatus] = useState<"idle" | "working" | "invalid" | "failed">("idle");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const parsed = Login.safeParse({ username: form.get("username"), password: form.get("password") });
    if (!parsed.success) {
      setStatus("invalid");
      return;
    }
    setStatus("working");
    try {
      const { data } = await api.POST("/v1/auth/login", { body: parsed.data });
      if (!data) {
        setStatus("failed");
        return;
      }
      saveToken(data.access_token);
      router.push(next);
    } catch {
      setStatus("failed");
    }
  }

  return (
    <form className="flex flex-col gap-4" onSubmit={submit} noValidate>
      <label className="flex flex-col gap-2 text-label font-semibold">
        {t("username")}
        <input name="username" autoComplete="username" className={FIELD} dir="ltr" />
      </label>
      <label className="flex flex-col gap-2 text-label font-semibold">
        {t("password")}
        <input name="password" type="password" autoComplete="current-password" className={FIELD} dir="ltr" />
      </label>
      {status === "invalid" || status === "failed" ? (
        <p role="alert" className="font-semibold text-danger">
          {t(status)}
        </p>
      ) : null}
      <BigButton type="submit" disabled={status === "working"}>
        {status === "working" ? t("working") : t("submit")}
      </BigButton>
    </form>
  );
}
