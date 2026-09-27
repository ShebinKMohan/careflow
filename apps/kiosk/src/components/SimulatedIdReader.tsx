"use client";

import { useTranslations } from "next-intl";

import { cardReader } from "@/devices/card-reader";
import { MOCK_CARDS, maskedCard } from "@/devices/mockCards";

/**
 * The dev panel (NEXT_PUBLIC_SIMULATORS=1) that stands in for an ID-card reader (M2-05). It is
 * labelled as a simulation so nobody mistakes it for a real ID service; each button taps one of the
 * seeded personas' synthetic cards.
 */
export function SimulatedIdReader() {
  const t = useTranslations("checkin");
  return (
    <section
      aria-label={t("simulatedReader")}
      className="rounded-card border-2 border-dashed border-border p-4"
      data-testid="simulated-id-reader"
    >
      <h2 className="mb-1 text-title font-semibold">{t("simulatedReader")}</h2>
      <p className="mb-3 text-label text-ink-muted">{t("simulatedReaderNote")}</p>
      <div className="flex flex-wrap gap-2">
        {MOCK_CARDS.map(({ persona, card }) => (
          <button
            key={persona}
            type="button"
            className="min-h-touch rounded-control border-2 border-primary px-4 font-semibold text-primary"
            onClick={() => cardReader.tap(card)}
          >
            {t("mockCard", { persona })} <bdi dir="ltr">{maskedCard(card.id_number)}</bdi>
          </button>
        ))}
      </div>
    </section>
  );
}
