import type { Schema } from "@careflow/contracts";

export type StructuredFacts = Schema<"StructuredFacts">;
export type Provenance = Schema<"Provenance">;

export interface FactView {
  factId: string;
  /** Where it sits in StructuredFacts, e.g. "vitals.heart_rate" or "symptoms.0". */
  path: string;
  value: string;
  provenance: Provenance;
  source?: Schema<"SourceRef">;
  recordedAt?: string;
  criticality?: string;
}

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

function isObject(value: unknown): value is { [key: string]: Json } {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** UCUM codes as people write them. */
const UNITS: Record<string, string> = { Cel: "°C", "[degF]": "°F", "mm[Hg]": "mmHg", "/min": "/min", "%": "%" };

function display(value: Json | undefined, unit: Json | undefined): string {
  if (isObject(value)) return String(value.display ?? value.code ?? "");
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (value === null || value === undefined) return "";
  if (typeof unit !== "string" || unit === "1") return String(value);
  const shown = UNITS[unit] ?? unit;
  return shown === "%" || shown.startsWith("°") ? `${String(value)}${shown}` : `${String(value)} ${shown}`;
}

/** Every fact in StructuredFacts (anything with a fact_id and a provenance), by fact id. */
export function indexFacts(facts: StructuredFacts | null | undefined): Map<string, FactView> {
  const index = new Map<string, FactView>();
  const walk = (node: Json, path: string) => {
    if (Array.isArray(node)) {
      node.forEach((child, i) => walk(child, path ? `${path}.${i}` : String(i)));
      return;
    }
    if (!isObject(node)) return;
    if (typeof node.fact_id === "string" && typeof node.provenance === "string") {
      index.set(node.fact_id, {
        factId: node.fact_id,
        path,
        value: display(node.value, node.unit),
        provenance: node.provenance as Provenance,
        ...(isObject(node.source) ? { source: node.source as unknown as Schema<"SourceRef"> } : {}),
        ...(typeof node.recorded_at === "string" ? { recordedAt: node.recorded_at } : {}),
        ...(typeof node.criticality === "string" ? { criticality: node.criticality } : {}),
      });
      return;
    }
    for (const [key, child] of Object.entries(node)) walk(child, path ? `${path}.${key}` : key);
  };
  if (facts) walk(facts as unknown as Json, "");
  return index;
}
