"use client";

import { useTranslations } from "next-intl";

import type { WorklistItem } from "@/lib/sortWorklist";

export function levelLabel(scale: string | undefined, level: number): string {
  return `${scale ?? "KTAS"} ${level}`;
}

/** "Suggested KTAS 2" (dashed: not a decision) or "Confirmed KTAS 2" (solid): never confused. */
export function LevelChip({ item }: { item: WorklistItem }) {
  const t = useTranslations("nurse");
  if (item.confirmed_level !== undefined) {
    return (
      <span className="whitespace-nowrap rounded-control bg-primary px-2 font-semibold text-on-primary">
        {t("confirmedChip", { label: levelLabel(item.scale, item.confirmed_level) })}
      </span>
    );
  }
  if (item.suggested_level !== undefined) {
    return (
      <span className="whitespace-nowrap rounded-control border-2 border-dashed border-border px-2">
        {t("suggestedChip", { label: levelLabel(item.scale, item.suggested_level) })}
      </span>
    );
  }
  return <span className="whitespace-nowrap rounded-control border-2 border-dashed border-border px-2">{t("noSuggestion")}</span>;
}
