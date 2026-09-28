import type { Schema } from "@careflow/contracts";

export type WorklistItem = Schema<"WorklistItem">;

/** A session with no suggestion sorts like level 2: never below a real level 3-5. */
export const NO_SUGGESTION_LEVEL = 2;

/** True while the nurse has not confirmed a level: its alerts are still active. */
export function awaitingDecision(item: WorklistItem): boolean {
  return item.confirmed_level === undefined;
}

/**
 * Alerts raised after the nurse's decision (M2-07): a new danger sign in a patient already waiting
 * with a level, for example from "I feel worse". They are active again until the nurse looks.
 */
export function alertsSinceDecision(item: WorklistItem): WorklistItem["alerts"] {
  if (awaitingDecision(item) || !item.decided_at) return [];
  return item.alerts.filter((alert) => raisedAfter(alert.raised_at, item.decided_at));
}

/** True when an alert raised at `raisedAt` came after the decision at `decidedAt`. */
export function raisedAfter(raisedAt: string | undefined, decidedAt: string | undefined): boolean {
  return raisedAt !== undefined && decidedAt !== undefined && Date.parse(raisedAt) > Date.parse(decidedAt);
}

function alertRank(item: WorklistItem): number {
  const active = awaitingDecision(item) ? item.alerts : alertsSinceDecision(item);
  if (active.some((alert) => alert.severity === "critical")) return 0;
  if (active.some((alert) => alert.severity === "high")) return 1;
  if (item.urgent_help) return 2;
  return 3;
}

/**
 * Danger first: active critical alerts, active high alerts, explicit urgent-help requests, then the level (the
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
