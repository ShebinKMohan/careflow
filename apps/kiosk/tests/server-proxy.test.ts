// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ configuredDevice: vi.fn() }));
vi.mock("@/lib/server/device", () => ({
  configuredDevice: mocks.configuredDevice,
  serverApiUrl: () => "http://clinic.internal:8000",
}));

import { GET as bootstrap } from "@/app/api/kiosk/bootstrap/route";
import { GET, POST } from "@/app/api/kiosk/[...path]/route";

const context = (path: string) => ({ params: Promise.resolve({ path: path.split("/") }) });
const request = (path: string, init?: RequestInit) => new Request(`http://localhost:3000/api/kiosk/${path}`, init);
const fetchMock = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  mocks.configuredDevice.mockResolvedValue({ token: "private-device-jwt", clinicId: "clinic-a", deviceId: "kiosk-a" });
});
afterEach(() => vi.unstubAllGlobals());

describe("server managed kiosk boundary", () => {
  it("bootstrap reveals only a noncredential marker and public installation identifiers", async () => {
    const response = await bootstrap();
    expect(response.headers.get("cache-control")).toBe("no-store");
    const data = await response.json();
    expect(data).toEqual({ token: "server-managed", clinicId: "clinic-a", deviceId: "kiosk-a" });
    expect(JSON.stringify(data)).not.toContain("private-device-jwt");
  });

  it("blocks staff, arbitrary session, traversal and auth endpoints before provisioning", async () => {
    for (const path of ["v1/auth/device-token", "v1/nurse/worklist", "v1/sessions/private/facts", "../v1/checkin/keypad", "v1/checkin/keypad/extra"]) {
      const response = await POST(request(path, { method: "POST", body: "{}" }), context(path));
      expect(response.status, path).toBe(404);
    }
    const response = await GET(request("v1/checkin/keypad"), context("v1/checkin/keypad"));
    expect(response.status).toBe(404);
    expect(mocks.configuredDevice).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects foreign origins and cross-site browser requests before provisioning", async () => {
    const cases: Record<string, string>[] = [{ origin: "https://foreign.example" }, { "sec-fetch-site": "cross-site" }];
    for (const headers of cases) {
      const response = await POST(request("v1/checkin/keypad", { method: "POST", headers, body: "{}" }), context("v1/checkin/keypad"));
      expect(response.status).toBe(403);
    }
    expect(mocks.configuredDevice).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("uses the installation credential upstream, not browser authorization, without forwarding private response headers", async () => {
    fetchMock.mockResolvedValue(new Response('{"session_id":"patient-session"}', { status: 201, headers: { "Set-Cookie": "private=secret" } }));
    const response = await POST(request("v1/checkin/keypad", { method: "POST", headers: { origin: "http://localhost:3000", authorization: "Bearer forged" }, body: '{"digits":"100001"}' }), context("v1/checkin/keypad"));
    expect(fetchMock).toHaveBeenCalledWith("http://clinic.internal:8000/v1/checkin/keypad", expect.objectContaining({
      body: '{"digits":"100001"}', headers: { Authorization: "Bearer private-device-jwt", "Content-Type": "application/json" },
    }));
    expect(response.status).toBe(201);
    expect(response.headers.get("set-cookie")).toBeNull();
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.text()).not.toContain("private-device-jwt");
  });

  it("rejects bodies over 8 KiB without calling the API", async () => {
    const response = await POST(request("v1/checkin/keypad", { method: "POST", body: "x".repeat(8193) }), context("v1/checkin/keypad"));
    expect(response.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows unavailable when provisioning is disabled or fails", async () => {
    mocks.configuredDevice.mockResolvedValueOnce(null);
    expect((await bootstrap()).status).toBe(503);
    mocks.configuredDevice.mockRejectedValueOnce(new Error("private configuration detail"));
    const response = await POST(request("v1/checkin/start/qr", { method: "POST", body: "{}" }), context("v1/checkin/start/qr"));
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain("private configuration detail");
  });

  it("never reads a session or measures vitals without that patient's credential", async () => {
    const id = "12345678-1234-1234-1234-123456789012";
    const status = await GET(request(`v1/sessions/${id}/status`), context(`v1/sessions/${id}/status`));
    const vitals = await POST(request(`v1/dev/sessions/${id}/vitals-sim`, { method: "POST", body: "{}" }), context(`v1/dev/sessions/${id}/vitals-sim`));
    expect(status.status).toBe(401);
    expect(vitals.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(mocks.configuredDevice).not.toHaveBeenCalled();
  });

  it.each([401, 403, 404])("rejects an invalid or different-session patient credential (API status %s)", async (status) => {
    const id = "12345678-1234-1234-1234-123456789012";
    fetchMock.mockResolvedValueOnce(new Response("{}", { status }));
    const response = await POST(request(`v1/dev/sessions/${id}/vitals-sim`, { method: "POST", headers: { "X-CareFlow-Patient": "wrong-session-token" }, body: "{}" }), context(`v1/dev/sessions/${id}/vitals-sim`));
    expect(response.status).toBe(403);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(`http://clinic.internal:8000/v1/sessions/${id}/status`, expect.objectContaining({ headers: { Authorization: "Bearer wrong-session-token" } }));
    expect(mocks.configuredDevice).not.toHaveBeenCalled();
  });

  it("serves patient status with the patient's scope, without using the device credential", async () => {
    const id = "12345678-1234-1234-1234-123456789012";
    fetchMock.mockResolvedValueOnce(new Response('{"state":"intake"}'));
    const response = await GET(request(`v1/sessions/${id}/status`, { headers: { "X-CareFlow-Patient": "valid-patient-token" } }), context(`v1/sessions/${id}/status`));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ state: "intake" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(mocks.configuredDevice).not.toHaveBeenCalled();
  });

  it("verifies the exact patient session before using the device credential to measure vitals", async () => {
    const id = "12345678-1234-1234-1234-123456789012";
    fetchMock.mockResolvedValueOnce(new Response('{"state":"intake"}'));
    fetchMock.mockResolvedValueOnce(new Response('{"role":"patient_session","clinic_id":"clinic-a"}'));
    fetchMock.mockResolvedValueOnce(new Response('{"simulated":true}', { status: 202 }));
    const response = await POST(request(`v1/dev/sessions/${id}/vitals-sim`, { method: "POST", headers: { "X-CareFlow-Patient": "valid-patient-token" }, body: '{"scenario":"normal"}' }), context(`v1/dev/sessions/${id}/vitals-sim`));
    expect(response.status).toBe(202);
    expect(fetchMock).toHaveBeenNthCalledWith(1, `http://clinic.internal:8000/v1/sessions/${id}/status`, expect.objectContaining({ headers: { Authorization: "Bearer valid-patient-token" } }));
    expect(fetchMock).toHaveBeenNthCalledWith(2, "http://clinic.internal:8000/v1/auth/me", expect.objectContaining({ headers: { Authorization: "Bearer valid-patient-token" } }));
    expect(fetchMock).toHaveBeenNthCalledWith(3, `http://clinic.internal:8000/v1/dev/sessions/${id}/vitals-sim`, expect.objectContaining({ headers: { Authorization: "Bearer private-device-jwt", "Content-Type": "application/json" } }));
  });

  it.each([
    { role: "patient_session", clinic_id: "another-clinic" },
    { role: "kiosk_device", clinic_id: "clinic-a" },
    { role: "nurse", clinic_id: "clinic-a" },
  ])("refuses vitals for a patient from another clinic or a nonpatient role ($role, $clinic_id)", async (identity) => {
    const id = "12345678-1234-1234-1234-123456789012";
    fetchMock.mockResolvedValueOnce(new Response('{"state":"intake"}'));
    fetchMock.mockResolvedValueOnce(Response.json(identity));
    const response = await POST(request(`v1/dev/sessions/${id}/vitals-sim`, { method: "POST", headers: { "X-CareFlow-Patient": "wrong-scope-token" }, body: '{"scenario":"normal"}' }), context(`v1/dev/sessions/${id}/vitals-sim`));
    expect(response.status).toBe(403);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/vitals-sim"))).toBe(false);
  });
});
