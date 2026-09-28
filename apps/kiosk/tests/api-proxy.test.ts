// @vitest-environment node
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";

import { afterEach, beforeEach, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ token: "server-managed" }));
vi.mock("@/lib/session", () => ({ readDevice: () => ({ token: state.token, clinicId: "clinic-a", deviceId: "kiosk-a" }) }));

beforeEach(() => { state.token = "server-managed"; });

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.resetModules();
});

it("omits the BFF-only patient proof on direct legacy-device API requests", async () => {
  state.token = "legacy-device-token";
  let received: { path?: string; authorization?: string; patientProof?: string | string[]; body: string } | null = null;
  const server = createServer((request, response) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk: string) => { body += chunk; });
    request.on("end", () => {
      received = { path: request.url, authorization: request.headers.authorization, patientProof: request.headers["x-careflow-patient"], body };
      response.writeHead(202, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ simulated: true }));
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    vi.stubGlobal("window", { location: { origin: "http://kiosk.example" } });
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", origin);
    const { api } = await import("@/lib/api");
    const result = await api.POST("/v1/dev/sessions/{session_id}/vitals-sim", {
      params: { path: { session_id: "ses_legacy" } },
      headers: { "X-CareFlow-Patient": "patient-proof-must-stay-local" },
      body: { scenario: "normal" },
    });
    expect(result.response.status).toBe(202);
    expect(received).toEqual({ path: "/v1/dev/sessions/ses_legacy/vitals-sim", authorization: "Bearer legacy-device-token", patientProof: undefined, body: '{"scenario":"normal"}' });
  } finally {
    await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  }
});

it("sends a bounded JSON body over HTTP/1 to the local kiosk proxy without a device credential", async () => {
  let received: { path?: string; authorization?: string; contentLength?: string; transferEncoding?: string; body: string } | null = null;
  const server = createServer((request, response) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk: string) => { body += chunk; });
    request.on("end", () => {
      received = { path: request.url, authorization: request.headers.authorization, contentLength: request.headers["content-length"], transferEncoding: request.headers["transfer-encoding"], body };
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ qr_token: "entry-ticket", expires_at: "2026-09-28T12:00:00Z" }));
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    vi.stubGlobal("window", { location: { origin } });
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://upstream.invalid");
    const { api } = await import("@/lib/api");
    const result = await api.POST("/v1/checkin/start/qr", { body: { language: "en" } });
    expect(result.data?.qr_token).toBe("entry-ticket");
    expect(received).toEqual({ path: "/api/kiosk/v1/checkin/start/qr", authorization: undefined, contentLength: String(Buffer.byteLength('{"language":"en"}')), transferEncoding: undefined, body: '{"language":"en"}' });
  } finally {
    await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  }
});
