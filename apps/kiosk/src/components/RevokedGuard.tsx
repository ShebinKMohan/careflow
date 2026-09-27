"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { PATIENT_REVOKED } from "@/lib/api";
import { useKiosk } from "@/lib/session";

/**
 * The API refused this patient's token (401): the visit continued on their phone (M2-06), even if
 * the kiosk missed the `session.handed_over` frame, or the token ran out. Forget the patient and
 * return to the welcome screen. A late 401 for an earlier patient's token changes nothing.
 */
export function RevokedGuard() {
  const router = useRouter();
  const { patient, wipe } = useKiosk();
  const token = patient?.token;
  useEffect(() => {
    if (!token) return undefined;
    const onRevoked = (event: Event) => {
      if ((event as CustomEvent<string>).detail !== token) return;
      wipe();
      router.push("/");
    };
    window.addEventListener(PATIENT_REVOKED, onRevoked);
    return () => window.removeEventListener(PATIENT_REVOKED, onRevoked);
  }, [token, wipe, router]);
  return null;
}
