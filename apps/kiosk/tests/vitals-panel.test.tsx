import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MEASUREMENT_MS, VitalsPanel } from "@/components/VitalsPanel";
import { MESSAGES } from "@/i18n/messages";

const post = vi.fn();
const get = vi.fn();
vi.mock("@/lib/api", () => ({ api: { POST: (...args: unknown[]) => post(...args), GET: (...args: unknown[]) => get(...args) } }));

const READING = {
  heart_rate: 104, spo2: 89, temperature_c: 37.1, systolic_bp: 128, diastolic_bp: 80,
  measured_at: "2026-09-28T09:03:01Z", device_id: "sim_vitals_01", simulated: true,
};
const snapshot = (vitals: unknown) => ({ data: { state: "intake", intake_revision: 1, vitals } });

function renderPanel(onDone = vi.fn(), locale: "en" | "ar" = "en") {
  const view = render(
    <NextIntlClientProvider locale={locale} messages={MESSAGES[locale]} timeZone="Asia/Dubai">
      <VitalsPanel sessionId="ses_test" patientToken="patient-proof" scenario="hypoxic" onDone={onDone} />
    </NextIntlClientProvider>,
  );
  return { ...view, onDone };
}
async function start() {
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Start demo measurement|Retry demo measurement/ })); });
}
async function advance(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
}
const value = (label: string) => within(screen.getByRole("region", { name: label })).getByTestId("vital-value");

describe("guided demo vitals", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    post.mockReset().mockResolvedValue({ data: { session_id: "ses_test", scenario: "hypoxic", frames: 31, simulated: true } });
    get.mockReset().mockResolvedValue(snapshot(READING)).mockResolvedValueOnce(snapshot(null));
  });
  afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); });

  it("labels the simulation honestly and reveals saved readings in stages, then advances once", async () => {
    const { onDone } = renderPanel();
    expect(screen.getByText("Demo readings")).toBeInTheDocument();
    expect(screen.getByText(/Mac Touch ID cannot measure/)).toBeInTheDocument();
    for (const label of ["Pulse", "Oxygen (SpO2)", "Blood pressure", "Temperature"]) expect(value(label)).toHaveTextContent("–");
    await start();
    expect(post).toHaveBeenCalledWith("/v1/dev/sessions/{session_id}/vitals-sim", {
      signal: expect.any(AbortSignal),
      headers: { "X-CareFlow-Patient": "patient-proof" }, params: { path: { session_id: "ses_test" } }, body: { scenario: "hypoxic" },
    });
    await advance(2_000);
    expect(value("Pulse")).toHaveTextContent("104");
    expect(value("Oxygen (SpO2)")).toHaveTextContent("–");
    expect(onDone).not.toHaveBeenCalled();
    await advance(2_000);
    expect(value("Oxygen (SpO2)")).toHaveTextContent("89");
    await advance(2_000);
    expect(value("Blood pressure")).toHaveTextContent("128/80");
    await advance(2_000);
    expect(value("Temperature")).toHaveTextContent("37.1");
    expect(onDone).not.toHaveBeenCalled();
    await advance(2_000);
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    await advance(20_000);
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("does not pass an old reading off as a completed new measurement", async () => {
    get.mockReset().mockResolvedValue(snapshot(READING));
    const { onDone } = renderPanel();
    await start();
    await advance(MEASUREMENT_MS);
    expect(value("Pulse")).toHaveTextContent("–");
    expect(onDone).not.toHaveBeenCalled();
    get.mockResolvedValue(snapshot({ ...READING, measured_at: "2026-09-28T09:03:02Z" }));
    await advance(1_000);
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("bounds the wait when the simulator never saves fresh readings", async () => {
    get.mockReset().mockResolvedValue(snapshot(READING));
    const { onDone } = renderPanel();
    await start();
    await advance(31_000);
    expect(screen.getByRole("alert")).toHaveTextContent("could not save a new demo reading");
    expect(onDone).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Retry demo measurement" })).toBeEnabled();
  });

  it("does not invent blood pressure when no cuff reading is available", async () => {
    get.mockResolvedValue(snapshot({ ...READING, systolic_bp: null, diastolic_bp: null }));
    const { onDone } = renderPanel();
    await start();
    await advance(MEASUREMENT_MS);
    expect(value("Blood pressure")).toHaveTextContent("–");
    expect(screen.getByText("Blood pressure is not included in this demo setup.")).toBeInTheDocument();
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("preserves the visit and permits retry after a failed start", async () => {
    post.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const { onDone } = renderPanel();
    await start();
    expect(screen.getByRole("alert")).toHaveTextContent("Your visit is safe");
    await advance(MEASUREMENT_MS);
    expect(onDone).not.toHaveBeenCalled();
    get.mockResolvedValueOnce(snapshot(null));
    await start();
    await advance(MEASUREMENT_MS);
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledTimes(2);
  });

  it("cancels automatic completion if polling fails after an earlier good reading", async () => {
    const { onDone } = renderPanel();
    await start();
    await advance(2_000);
    get.mockRejectedValueOnce(new TypeError("network offline"));
    await advance(MEASUREMENT_MS);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(onDone).not.toHaveBeenCalled();
  });

  it("does not complete with incomplete or non-simulated readings", async () => {
    get.mockResolvedValue(snapshot({ ...READING, simulated: false }));
    const { onDone } = renderPanel();
    await start();
    await advance(MEASUREMENT_MS);
    expect(onDone).not.toHaveBeenCalled();
    get.mockResolvedValue(snapshot({ ...READING, temperature_c: null }));
    await advance(1_000);
    expect(onDone).not.toHaveBeenCalled();
  });

  it("cancels polling and completion when leaving the page", async () => {
    const { unmount, onDone } = renderPanel();
    await start();
    unmount();
    const calls = get.mock.calls.length;
    await advance(30_000);
    expect(get).toHaveBeenCalledTimes(calls);
    expect(onDone).not.toHaveBeenCalled();
  });

  it("aborts a stalled request and offers retry within the absolute deadline", async () => {
    get.mockReset().mockReturnValue(new Promise(() => undefined));
    const { onDone } = renderPanel();
    await start();
    const signal = get.mock.calls[0]![1].signal as AbortSignal;
    await advance(35_000);
    expect(signal.aborted).toBe(true);
    expect(screen.getByRole("button", { name: "Retry demo measurement" })).toBeEnabled();
    expect(onDone).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });

  it("provides Arabic instructions and an accessible start action", () => {
    renderPanel(vi.fn(), "ar");
    expect(screen.getByRole("button", { name: "ابدأ القياس التجريبي" })).toBeInTheDocument();
    expect(screen.getByText("قراءات تجريبية")).toBeInTheDocument();
  });
});
