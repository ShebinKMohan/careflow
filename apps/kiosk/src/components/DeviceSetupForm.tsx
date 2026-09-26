"use client";

import type { Schema } from "@careflow/contracts";
import { BigButton } from "@careflow/ui";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { type FormEvent, useState } from "react";
import { z } from "zod";

import { api } from "@/lib/api";
import { saveDevice } from "@/lib/session";

const DeviceSetup = z.object({
  device_id: z
    .string()
    .trim()
    .min(1)
    .max(64)
    .regex(/^[A-Za-z0-9][A-Za-z0-9_.:-]*$/),
  device_secret: z.string().min(16).max(256),
});

const FIELD = "min-h-touch w-full rounded-control border-2 border-border bg-surface px-4 text-body";

/** Stub kiosk sign-in: device ID + secret -> kiosk_device token (POST /v1/auth/device-token). */
export function DeviceSetupForm() {
  const t = useTranslations("setup");
  const router = useRouter();
  const [status, setStatus] = useState<"idle" | "working" | "invalid" | "failed">("idle");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const parsed = DeviceSetup.safeParse({ device_id: form.get("device_id"), device_secret: form.get("device_secret") });
    if (!parsed.success) {
      setStatus("invalid");
      return;
    }
    setStatus("working");
    const body = { ...parsed.data, device_type: "kiosk" } satisfies Schema<"DeviceTokenRequest">;
    const { data } = await api.POST("/v1/auth/device-token", { body });
    if (!data) {
      setStatus("failed");
      return;
    }
    saveDevice({ token: data.access_token, clinicId: data.clinic_id, deviceId: parsed.data.device_id });
    router.push("/checkin");
  }

  return (
    <form className="flex flex-col gap-4" onSubmit={submit} noValidate>
      <label className="flex flex-col gap-2 text-label font-semibold">
        {t("deviceId")}
        <input name="device_id" autoComplete="off" className={FIELD} dir="ltr" />
      </label>
      <label className="flex flex-col gap-2 text-label font-semibold">
        {t("deviceSecret")}
        <input name="device_secret" type="password" autoComplete="off" className={FIELD} dir="ltr" />
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
