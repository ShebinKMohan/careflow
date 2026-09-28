// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { isCloudPreview } from "@/lib/server/preview";

beforeEach(() => {
  for (const name of ["VERCEL", "CAREFLOW_API_URL", "NEXT_PUBLIC_API_BASE_URL", "CAREFLOW_KIOSK_AUTO_SETUP", "CAREFLOW_KIOSK_DEVICE_SECRET", "CAREFLOW_DEV_DEVICE_SECRET"]) vi.stubEnv(name, "");
});
afterEach(() => vi.unstubAllEnvs());

describe("server-derived cloud preview", () => {
  it("preserves local startup when no cloud or device configuration is present", () => {
    expect(isCloudPreview()).toBe(false);
  });

  it("detects an unconfigured Vercel deployment immediately", () => {
    vi.stubEnv("VERCEL", "1");
    expect(isCloudPreview()).toBe(true);
  });

  it("preserves a configured cloud installation and requires both API and device settings", () => {
    vi.stubEnv("VERCEL", "1");
    vi.stubEnv("CAREFLOW_API_URL", "https://api.example.test");
    vi.stubEnv("CAREFLOW_KIOSK_AUTO_SETUP", "1");
    vi.stubEnv("CAREFLOW_KIOSK_DEVICE_SECRET", "test-server-only-secret");
    expect(isCloudPreview()).toBe(false);
    vi.stubEnv("CAREFLOW_API_URL", "");
    expect(isCloudPreview()).toBe(true);
    vi.stubEnv("CAREFLOW_API_URL", "https://api.example.test");
    vi.stubEnv("CAREFLOW_KIOSK_DEVICE_SECRET", "");
    expect(isCloudPreview()).toBe(true);
  });
});
