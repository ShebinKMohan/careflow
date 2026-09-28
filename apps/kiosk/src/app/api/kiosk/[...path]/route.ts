import { configuredDevice, serverApiUrl } from "@/lib/server/device";

export const dynamic = "force-dynamic";
const POST_PATHS = new Set(["v1/checkin/keypad", "v1/checkin/id-tap", "v1/checkin/start/qr", "v1/reception/checkin", "v1/identity/lookup", "v1/identity/verify", "v1/checkin/adaptive", "v1/checkin/urgent"]);
const SESSION = "[0-9a-fA-F-]{36}";

async function forward(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const path = (await context.params).path.join("/");
  const permitted = request.method === "POST"
    ? POST_PATHS.has(path) || new RegExp(`^v1/dev/sessions/${SESSION}/vitals-sim$`).test(path)
    : request.method === "GET" && (path === "v1/reception/directory" || new RegExp(`^v1/sessions/${SESSION}/status$`).test(path));
  if (!permitted) return Response.json({ error: "Not found" }, { status: 404 });
  const origin = request.headers.get("origin");
  if ((origin && origin !== new URL(request.url).origin) || request.headers.get("sec-fetch-site") === "cross-site") {
    return Response.json({ error: "Origin not allowed" }, { status: 403 });
  }
  try {
    const sessionId = /\/sessions\/([0-9a-fA-F-]{36})\//.exec(path)?.[1];
    const patientToken = request.headers.get("X-CareFlow-Patient");
    if (sessionId) {
      if (!patientToken) return Response.json({ error: "Patient session required" }, { status: 401 });
      const verified = await fetch(`${serverApiUrl()}/v1/sessions/${sessionId}/status`, {
        headers: { Authorization: `Bearer ${patientToken}` }, cache: "no-store", signal: AbortSignal.timeout(8_000),
      });
      if (!verified.ok) return Response.json({ error: "Patient session unavailable" }, { status: verified.status >= 500 ? 503 : 403 });
      if (request.method === "GET") return new Response(await verified.text(), {
        status: 200, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
      });
    }
    const device = await configuredDevice();
    if (!device) return Response.json({ error: "Kiosk unavailable" }, { status: 503 });
    if (sessionId) {
      const identity = await fetch(`${serverApiUrl()}/v1/auth/me`, {
        headers: { Authorization: `Bearer ${patientToken}` }, cache: "no-store", signal: AbortSignal.timeout(8_000),
      });
      const me = identity.ok ? await identity.json() as { role?: string; clinic_id?: string } : null;
      if (me?.role !== "patient_session" || me.clinic_id !== device.clinicId) {
        return Response.json({ error: "Patient does not belong to this kiosk" }, { status: 403 });
      }
    }
    const body = request.method === "POST" ? await request.text() : undefined;
    if (body && new TextEncoder().encode(body).byteLength > 8192) return Response.json({ error: "Request too large" }, { status: 413 });
    const upstream = await fetch(`${serverApiUrl()}/${path}`, {
      method: request.method, body, cache: "no-store",
      headers: { Authorization: `Bearer ${device.token}`, "Content-Type": "application/json" },
      signal: AbortSignal.timeout(30_000),
    });
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    return Response.json({ error: "Clinic connection unavailable" }, { status: 503 });
  }
}
export const GET = forward;
export const POST = forward;
