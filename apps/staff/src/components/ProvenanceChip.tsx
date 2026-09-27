"use client";

import { useTranslations } from "next-intl";

import type { Provenance } from "@/lib/facts";

/** Colour per source, always with its text label (never colour alone). A model-inferred chip has
 * a dashed border; a source that failed the check is red and says so. */
const STYLE: Record<Provenance | "missing", string> = {
  EHR: "border-solid border-prov-ehr bg-prov-ehr-tint text-prov-ehr",
  PATIENT_STATED: "border-solid border-prov-patient bg-prov-patient-tint text-prov-patient",
  DEVICE_MEASURED: "border-solid border-prov-device bg-prov-device-tint text-prov-device",
  NURSE_CONFIRMED: "border-solid border-prov-nurse bg-prov-nurse-tint text-prov-nurse",
  MODEL_INFERRED: "border-dashed border-prov-model bg-prov-model-tint text-prov-model",
  missing: "border-solid border-prov-missing bg-prov-missing-tint text-prov-missing",
};

export function ProvenanceChip({ provenance, missing = false }: { provenance: Provenance; missing?: boolean }) {
  const t = useTranslations("provenance");
  return (
    <span
      className={`inline-flex items-center whitespace-nowrap rounded-control border-2 px-2 text-label font-semibold ${
        STYLE[missing ? "missing" : provenance]
      }`}
      data-provenance={provenance}
      data-missing={missing ? "true" : undefined}
    >
      {missing ? t("missing") : t(provenance)}
    </span>
  );
}
