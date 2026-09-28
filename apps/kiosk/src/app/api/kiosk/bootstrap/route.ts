import { configuredDevice } from "@/lib/server/device";

export const dynamic = "force-dynamic";
export async function GET() {
  try {
    const device = await configuredDevice();
    if (!device) return Response.json({ available: false }, { status: 503 });
    // The marker is not a credential. Only this server can authenticate the kiosk.
    return Response.json({ token: "server-managed", clinicId: device.clinicId, deviceId: device.deviceId }, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch {
    return Response.json({ available: false }, { status: 503 });
  }
}
