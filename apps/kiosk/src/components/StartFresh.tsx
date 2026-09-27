"use client";

import { useEffect } from "react";

import { useKiosk } from "@/lib/session";

/** The welcome screen belongs to the next patient: forget any previous one. */
export function StartFresh() {
  const { wipe } = useKiosk();
  useEffect(() => wipe(), [wipe]);
  return null;
}
