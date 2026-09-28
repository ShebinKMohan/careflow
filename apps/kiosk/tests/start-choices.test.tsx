import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StartChoices } from "@/components/StartChoices";
import { MESSAGES } from "@/i18n/messages";

const { post, push, device } = vi.hoisted(() => ({ post: vi.fn(), push: vi.fn(), device: { ready: true } }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/api", () => ({ api: { POST: (...args: unknown[]) => post(...args) } }));
vi.mock("@/lib/session", () => ({ readDevice: () => device.ready ? { token: "server-managed" } : null, subscribeDevice: () => () => undefined }));
vi.mock("@/components/ContinueOnPhone", () => ({ COMPANION_URL: "http://clinic.local:3002" }));
vi.mock("qrcode.react", () => ({ QRCodeSVG: ({ value }: { value: string }) => <svg data-testid="qr-value" data-value={value} /> }));

const reply = () => ({ data: { qr_token: "entry-code", expires_at: new Date(Date.now() + 600_000).toISOString() } });
const show = (locale: "en" | "ar" = "en") => render(<NextIntlClientProvider locale={locale} messages={MESSAGES[locale]} timeZone="UTC"><StartChoices /></NextIntlClientProvider>);

beforeEach(() => { post.mockReset().mockResolvedValue(reply()); push.mockClear(); device.ready = true; });

describe("welcome entry choices", () => {
  it("offers both routes immediately and issues a localized entry QR with the token only in the fragment", async () => {
    show("ar");
    expect(screen.getByRole("heading", { name: MESSAGES.ar.experience.phoneTitle })).toBeInTheDocument();
    const code = await screen.findByTestId("qr-value");
    expect(code).toHaveAttribute("data-value", "http://clinic.local:3002/start?l=ar#t=entry-code");
    expect(post).toHaveBeenCalledWith("/v1/checkin/start/qr", expect.objectContaining({ body: { language: "ar" }, signal: expect.any(AbortSignal) }));
    await userEvent.click(screen.getByRole("button", { name: MESSAGES.ar.experience.kioskAction }));
    expect(push).toHaveBeenCalledWith("/checkin");
  });

  it("keeps kiosk check-in usable when QR creation fails and lets the user retry", async () => {
    post.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    show();
    expect(await screen.findByText(MESSAGES.en.experience.qrUnavailable)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: MESSAGES.en.experience.kioskAction })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: MESSAGES.en.experience.qrRetry }));
    await waitFor(() => expect(screen.getByTestId("qr-value")).toBeInTheDocument());
    expect(post).toHaveBeenCalledTimes(2);
  });

  it("does not issue an anonymous QR request before automatic device setup is ready", () => {
    device.ready = false;
    show();
    expect(screen.getByText(MESSAGES.en.experience.qrLoading)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: MESSAGES.en.experience.kioskAction })).toBeDisabled();
  });
});
