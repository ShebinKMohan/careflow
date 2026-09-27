"use client";

import { useTranslations } from "next-intl";

import type { WorklistItem } from "@/lib/sortWorklist";

/** Product names of the urgency models (not translated). */
const MODEL_NAMES: Record<string, string> = { laya: "Laya", hgb: "HGB" };

export function levelLabel(scale: string | undefined, level: number): string {
  return `${scale ?? "KTAS"} ${level}`;
}

/** "Suggested KTAS 2" (dashed: not a decision) or "Confirmed KTAS 2" (solid): never confused.
 * A suggestion names its model and where it ran (M3-02): "Laya 1.0.0 · kiosk", "HGB 1.0.0 · server". */
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
    const source = item.suggestion_source;
    return (
      <span className="inline-flex flex-col items-start gap-1">
        <span className="whitespace-nowrap rounded-control border-2 border-dashed border-border px-2">
          {t("suggestedChip", { label: levelLabel(item.scale, item.suggested_level) })}
        </span>
        {source ? (
          <span className="whitespace-nowrap text-label text-ink-muted" data-testid="suggestion-source">
            {t("suggestionSource", {
              model: MODEL_NAMES[source.family] ?? source.family,
              version: source.version,
              device: t(`devices.${source.device}`),
            })}
          </span>
        ) : null}
      </span>
    );
  }
  return <span className="whitespace-nowrap rounded-control border-2 border-dashed border-border px-2">{t("noSuggestion")}</span>;
}
