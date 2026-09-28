import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PatientReceipt } from "@/components/PatientReceipt";
import { MESSAGES } from "@/i18n/messages";

const mocks = vi.hoisted(() => ({ get: vi.fn(), patient: { sessionId: "session-a", token: "private-token", language: "en" } }));
vi.mock("@/lib/api", () => ({ patientApi: () => ({ GET: mocks.get }) }));
vi.mock("@/lib/session", () => ({ useKiosk: () => ({ patient: mocks.patient }) }));
function mount(compact = false) { return render(<NextIntlClientProvider locale="en" messages={MESSAGES.en}><PatientReceipt compact={compact} /></NextIntlClientProvider>); }
const base = { session_id: "session-a", visit_reference: "CF-1234ABCD", nurse_confirmed: false, facts: [], alerts: [], suggestion: null, warnings: [] };
afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

describe("patient's scoped receipt", () => {
  it("labels the reference separately from a queue token and missing data as missing", async () => {
    mocks.get.mockResolvedValue({ data: base }); mount();
    expect(await screen.findByText("CF-1234ABCD")).toBeInTheDocument();
    expect(screen.getByText(MESSAGES.en.patientReceipt.referenceHint)).toBeInTheDocument();
    expect(screen.getAllByText("Not recorded")).toHaveLength(5);
    expect(screen.getByText(MESSAGES.en.patientReceipt.noSuggestion)).toBeInTheDocument();
    expect(screen.queryByText(/Normal/)).not.toBeInTheDocument();
  });
  it("shows an actual provisional model identity and rule code without inventing confirmation", async () => {
    mocks.get.mockResolvedValue({ data: { ...base, alerts: [{ code: "FAST_STROKE", severity: "critical" }], suggestion: { model_id: "laya-ktas", model_version: "artifact-42", level: 2, scale: "KTAS", confidence: .72, reasons: [{ text: "Pulse supported this prediction", fact_ids: ["pulse"] }] } } }); mount();
    expect(await screen.findByText("FAST_STROKE")).toBeInTheDocument();
    expect(screen.getByText("laya-ktas")).toBeInTheDocument();
    expect(screen.getByText("KTAS 2")).toBeInTheDocument();
    expect(screen.getByText(MESSAGES.en.patientReceipt.notDiagnosis)).toBeInTheDocument();
    expect(screen.queryByText(MESSAGES.en.patientReceipt.confirmed)).not.toBeInTheDocument();
    expect(screen.getByText(MESSAGES.en.patientReceipt.alertHint)).toBeInTheDocument();
  });
  it("preserves zero values and labels simulated device provenance", async () => {
    mocks.get.mockResolvedValue({ data: { ...base, facts: [{ key: "pain_score", fact_id: "pain", value: "0", provenance: "PATIENT_STATED", simulated: false }, { key: "vitals.heart_rate", fact_id: "pulse", value: "82 /min", provenance: "DEVICE_MEASURED", simulated: true }] } }); mount();
    expect(await screen.findByText("0")).toBeInTheDocument();
    expect(screen.getByText("Device · Simulated")).toBeInTheDocument();
    expect(screen.getByText("You told us")).toBeInTheDocument();
    expect(screen.getByText(MESSAGES.en.patientReceipt.cameraStatus)).toBeInTheDocument();
  });
  it("does not render a different patient's response", async () => {
    mocks.get.mockResolvedValue({ data: { ...base, session_id: "another", visit_reference: "PRIVATE-OTHER" } }); mount();
    await waitFor(() => expect(mocks.get).toHaveBeenCalled());
    expect(screen.queryByText("PRIVATE-OTHER")).not.toBeInTheDocument();
  });
  it("compact receipt shows urgent codes and reference but never labels an absent alert as safe", async () => {
    mocks.get.mockResolvedValue({ data: { ...base, alerts: [{ code: "CHEST_PAIN_ECG_10MIN", severity: "high" }] } }); mount(true);
    expect(await screen.findByText("CHEST_PAIN_ECG_10MIN")).toBeInTheDocument();
    expect(screen.getByText("CF-1234ABCD")).toBeInTheDocument();
    expect(screen.queryByText(MESSAGES.en.patientReceipt.suggestion)).not.toBeInTheDocument();
  });
});
