import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DeviceSetupForm } from "@/components/DeviceSetupForm";
import { MESSAGES } from "@/i18n/messages";
import { API_BASE_URL } from "@/lib/api";
import { readDevice, saveDevice } from "@/lib/session";

const { fetchMock, push } = vi.hoisted(() => {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, push: vi.fn() };
});
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

async function fillSetup() {
  render(<NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC"><DeviceSetupForm /></NextIntlClientProvider>);
  await userEvent.type(screen.getByLabelText("Device ID"), "kiosk_test");
  await userEvent.type(screen.getByLabelText("Device secret"), "synthetic-device-secret");
  await userEvent.click(screen.getByRole("button", { name: "Sign in kiosk" }));
}

beforeEach(() => {
  push.mockClear();
  sessionStorage.clear();
  saveDevice({ token: "server-managed", deviceId: "old_kiosk", clinicId: "clinic_demo" });
  fetchMock.mockReset().mockImplementation(async () => new Response(JSON.stringify({ access_token: "new-device-token", clinic_id: "clinic_demo" }), { status: 200, headers: { "content-type": "application/json" } }));
});

describe("explicit staff kiosk setup", () => {
  it("authenticates directly without inheriting the current server-managed device", async () => {
    await fillSetup();
    await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
    const request = fetchMock.mock.calls[0]?.[0] as Request;
    expect(request.url).toBe(`${API_BASE_URL}/v1/auth/device-token`);
    expect(request.headers.has("Authorization")).toBe(false);
    expect(readDevice()?.token).toBe("new-device-token");
  });

  it("shows a recoverable error when authentication cannot reach the API", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await fillSetup();
    expect(await screen.findByRole("alert")).toHaveTextContent(MESSAGES.en.setup.failed);
    expect(screen.getByRole("button", { name: "Sign in kiosk" })).toBeEnabled();
    expect(push).not.toHaveBeenCalled();
    expect(readDevice()?.token).toBe("server-managed");
  });
});
