"use client";

import { useTranslations } from "next-intl";

import type { Provenance } from "@/lib/facts";

/** Where a fact came from, as text (never colour alone); the code stays machine-readable. */
export function ProvenanceChip({ provenance }: { provenance: Provenance }) {
  const t = useTranslations("provenance");
  return (
    <span
      className="whitespace-nowrap rounded-control border border-border bg-secondary px-2 text-label text-on-secondary"
      data-provenance={provenance}
    >
      {t(provenance)}
    </span>
  );
}
