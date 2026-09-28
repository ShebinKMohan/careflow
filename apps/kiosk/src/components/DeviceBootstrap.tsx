"use client";

import { useEffect } from "react";
import { readDevice, saveDevice } from "@/lib/session";

/** Installation setup belongs to the server, never to a patient in the check-in flow. */
export function DeviceBootstrap() {
  useEffect(() => {
    let cancelled = false;
    let working = false;
    const provision = (force = false) => {
      const existing = readDevice();
      let expired = false;
      if (existing && existing.token !== "server-managed") {
        try {
          // An expiry hint only; the API remains the authority that validates credentials.
          const encoded = existing.token.split(".")[1];
          const claims = JSON.parse(atob((encoded ?? "").replace(/-/g, "+").replace(/_/g, "/"))) as { exp?: number };
          expired = typeof claims.exp === "number" && claims.exp * 1000 <= Date.now() + 30_000;
        } catch { /* Retain explicitly provisioned opaque credentials. */ }
      }
      if ((existing && !expired && !force) || working) return;
      working = true;
      void fetch("/api/kiosk/bootstrap", { cache: "no-store", signal: AbortSignal.timeout(10_000) }).then(async (response) => {
        if (!response.ok) return;
        const data = await response.json() as { token?: string; clinicId?: string; deviceId?: string };
        if (!cancelled && data.token === "server-managed" && data.clinicId && data.deviceId) {
          saveDevice({ token: data.token, clinicId: data.clinicId, deviceId: data.deviceId });
        }
      }).catch(() => undefined).finally(() => { working = false; });
    };
    provision();
    const retry = () => provision(true);
    window.addEventListener("careflow:retry-device", retry);
    return () => { cancelled = true; window.removeEventListener("careflow:retry-device", retry); };
  }, []);
  return null;
}
