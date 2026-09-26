import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { VitalsPanel } from "@/components/VitalsPanel";
import { MESSAGES } from "@/i18n/messages";

const post = vi.fn();
const get = vi.fn();
vi.mock("@/lib/api", () => ({ api: { POST: (...args: unknown[]) => post(...args), GET: (...args: unknown[]) => get(...args) } }));

const READING = {
  heart_rate: 104,
  spo2: 89,
  temperature_c: 37.1,
  systolic_bp: 128,
  diastolic_bp: 80,
  measured_at: "2026-09-26T09:03:00Z",
  device_id: "sim_vitals_01",
  simulated: true,
};

function renderPanel(scenario: "hypoxic" | null = null, locale: "en" | "ar" = "en") {
  return render(
    <NextIntlClientProvider locale={locale} messages={MESSAGES[locale]} timeZone="Asia/Dubai">
      <VitalsPanel sessionId="ses_test" scenario={scenario} />
    </NextIntlClientProvider>,
  );
}

describe("VitalsPanel", () => {
  beforeEach(() => {
    post.mockReset().mockResolvedValue({ data: { session_id: "ses_test", scenario: "hypoxic", frames: 31, simulated: true } });
    get.mockReset().mockResolvedValue({ data: { state: "intake", vitals: READING } });
  });

  it("renders the four tiles, the simulated badge and the exact skin-tone text", async () => {
    renderPanel("hypoxic");
    expect(screen.getByText("Simulated device")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Measure vitals" }));
    const tiles = ["Pulse", "Oxygen (SpO2)", "Temperature", "Blood pressure"];
    for (const label of tiles) expect(await screen.findByRole("region", { name: label })).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Oxygen (SpO2)" })).getByTestId("vital-value")).toHaveTextContent("89");
    expect(within(screen.getByRole("region", { name: "Blood pressure" })).getByTestId("vital-value")).toHaveTextContent("128/80");
    expect(screen.getByText("Pulse oximeter: warns about known skin-tone limits")).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith("/v1/dev/sessions/{session_id}/vitals-sim", {
      params: { path: { session_id: "ses_test" } },
      body: { scenario: "hypoxic" },
    });
  });

  it("shows no blood-pressure tile when the kiosk measures pulse, SpO2 and temperature only", async () => {
    get.mockResolvedValue({ data: { state: "intake", vitals: { ...READING, systolic_bp: undefined, diastolic_bp: undefined } } });
    renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "Measure vitals" }));
    expect(await screen.findByRole("region", { name: "Pulse" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Blood pressure" })).not.toBeInTheDocument();
    expect(post).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ body: {} }));
  });

  it("reports a device that does not start", async () => {
    post.mockResolvedValue({ error: { status: 404 } });
    renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "Measure vitals" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The vitals device could not start.");
  });
});
