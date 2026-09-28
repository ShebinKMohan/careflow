import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { expect, it, vi } from "vitest";
import { Worklist } from "@/components/Worklist";
import { MESSAGES } from "@/i18n/messages";
import { sortWorklist, type WorklistItem } from "@/lib/sortWorklist";

const { live } = vi.hoisted(() => ({ live: vi.fn() }));
vi.mock("@/lib/useLiveWorklist", () => ({ useLiveWorklist: live }));
vi.mock("@/lib/api", () => ({ api: { GET: vi.fn(), POST: vi.fn() } }));
const normal: WorklistItem = { session_id: "normal", patient_display: "A. B.", arrived_at: "2026-09-29T09:00:00Z", state: "awaiting_nurse", alerts: [], open_tasks: [], suggested_level: 1 };
const urgent: WorklistItem = { ...normal, session_id: "abcdef12-0000-4000-8000-000000000001", patient_display: "Unknown", suggested_level: undefined, urgent_help: { requested_at: "2026-09-29T09:02:00Z", patient_type: "adult", emergency_symptoms: [] } };

it("prioritizes explicit help requests after clinical alerts without inventing a severity", () => {
  const critical: WorklistItem = { ...normal, session_id: "critical", alerts: [{ alert_id: "a1", rule_id: "FAST_STROKE", severity: "critical", partial: false }] };
  const high: WorklistItem = { ...normal, session_id: "high", alerts: [{ alert_id: "a2", rule_id: "NG143_AMBER_FLAG", severity: "high", partial: false }] };
  expect(sortWorklist([normal, urgent, high, critical]).map(row => row.session_id)).toEqual(["critical", "high", urgent.session_id, "normal"]);
  expect(urgent.alerts).toEqual([]);
  expect(urgent.suggested_level).toBeUndefined();
});

it("keeps an unidentified help request visible when search hides its row and includes it in attention filters", async () => {
  live.mockReturnValue({ state: { status: "ready", data: [urgent, normal] }, live: "live", refresh: vi.fn() });
  render(<NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC"><Worklist /></NextIntlClientProvider>);
  const requests = screen.getByTestId("urgent-help-requests");
  expect(requests).toHaveTextContent("Patient not yet identified");
  expect(requests).toHaveTextContent("CF-ABCDEF12");
  expect(requests).toHaveTextContent("No symptoms selected; staff assessment needed.");
  expect(requests).not.toHaveTextContent("Critical");
  await userEvent.click(screen.getByRole("button", { name: MESSAGES.en.worklistTools.alerted }));
  expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(2);
  await userEvent.type(screen.getByRole("searchbox"), "cannot-match-any-patient");
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  expect(screen.getByTestId("urgent-help-requests")).toBeVisible();
  expect(within(requests).getByRole("link", { name: "Open assessment" })).toHaveAttribute("href", `/nurse/${urgent.session_id}`);
});
