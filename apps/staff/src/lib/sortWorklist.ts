import type { Schema } from "@careflow/contracts";

export type WorklistItem = Schema<"WorklistItem">;

/** A session with no suggestion sorts like level 2: never below a real level 3-5. */
export const NO_SUGGESTION_LEVEL = 2;

/** True while the nurse has not confirmed a level: its alerts are still active. */
export function awaitingDecision(item: WorklistItem): boolean {
  return item.confirmed_level === undefined;
}

function alertRank(item: WorklistItem): number {
  if (!awaitingDecision(item)) return 2;
  if (item.alerts.some((alert) => alert.severity === "critical")) return 0;
  if (item.alerts.some((alert) => alert.severity === "high")) return 1;
  return 2;
}

/**
 * Danger first: an active critical alert, then an active high alert, then the level (the
 * confirmed one once decided, else the suggestion, 1 first; no suggestion counts as 2), then the
 * earliest arrival.
 */
export function sortWorklist(items: readonly WorklistItem[]): WorklistItem[] {
  return [...items].sort(
    (a, b) =>
      alertRank(a) - alertRank(b) ||
      level(a) - level(b) ||
      Date.parse(a.arrived_at) - Date.parse(b.arrived_at) ||
      a.session_id.localeCompare(b.session_id),
  );
}

function level(item: WorklistItem): number {
  return item.confirmed_level ?? item.suggested_level ?? NO_SUGGESTION_LEVEL;
}
