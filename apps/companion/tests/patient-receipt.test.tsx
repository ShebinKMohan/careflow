import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, expect, it, vi } from "vitest";
import { PatientReceipt } from "@/components/PatientReceipt";
import { MESSAGES } from "@/i18n/messages";
import { saveSession } from "@/lib/session";
const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: { GET: get } }));
const receipt = { session_id: "ses_test", visit_reference: "CF-DEMO-001", nurse_confirmed: false, assistance_requested: false, facts: [], alerts: [], suggestion: null, warnings: [] };
beforeEach(() => { get.mockReset(); sessionStorage.clear(); saveSession({ sessionId: "ses_test", language: "en", display: "D. E." }); });
function show(compact = false) { render(<NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC"><PatientReceipt compact={compact} /></NextIntlClientProvider>); }
it("uses the saved session's actual reference and explicitly leaves missing vital signs unrecorded", async () => {
  get.mockResolvedValue({ data: receipt }); show();
  expect(await screen.findByText("CF-DEMO-001")).toBeInTheDocument();
  expect(screen.getByText(/Your queue token appears after/)).toBeInTheDocument();
  expect(screen.getByText("No model suggestion is available yet.", { exact: false })).toBeInTheDocument();
  expect(screen.getAllByText("Not recorded")).toHaveLength(5);
  expect(screen.getByText("Phone camera is off. No camera assessment was recorded.")).toBeInTheDocument();
});
it("shows a saved anonymous nurse request and reference during intake even without a rule alert", async () => {
  get.mockResolvedValue({ data: { ...receipt, assistance_requested: true } }); show(true);
  expect(await screen.findByText("Urgent help request recorded for the nurse")).toBeInTheDocument();
  expect(screen.getByText("CF-DEMO-001")).toBeInTheDocument();
  expect(screen.queryByText("Suggested urgency")).not.toBeInTheDocument();
});
it("rejects a response for another session instead of displaying that receipt", async () => {
  get.mockResolvedValue({ data: { ...receipt, session_id: "ses_someone_else" } }); show();
  expect(await screen.findByRole("status")).toHaveTextContent("could not refresh");
  expect(screen.queryByText("CF-DEMO-001")).not.toBeInTheDocument();
});

it("refreshes immediately when the queue status changes instead of waiting for the poll", async () => {
  get.mockResolvedValue({ data: receipt });
  const view = render(<NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC"><PatientReceipt refreshKey="awaiting_nurse:" /></NextIntlClientProvider>);
  await screen.findByText("Awaiting nurse review");
  get.mockResolvedValue({ data: { ...receipt, nurse_confirmed: true, confirmed_scale: "MTS", confirmed_level: 3 } });
  view.rerender(<NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC"><PatientReceipt refreshKey="queued:token-one" /></NextIntlClientProvider>);
  expect(await screen.findByText("MTS 3")).toBeInTheDocument();
  expect(get).toHaveBeenCalledTimes(2);
});
