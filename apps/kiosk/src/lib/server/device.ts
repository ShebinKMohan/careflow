import { createHash } from "node:crypto";

const NAMESPACE = "7b0f1f7e5c1a4c4e9f3e2f8a8f0c0a55";
function seededKioskId(): string {
  const bytes = createHash("sha1").update(Buffer.concat([Buffer.from(NAMESPACE, "hex"), Buffer.from("clinic-a/kiosk")])).digest().subarray(0, 16);
  bytes[6] = (bytes[6]! & 15) | 80;
  bytes[8] = (bytes[8]! & 63) | 128;
  const h = bytes.toString("hex");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

export const serverApiUrl = () => (process.env.CAREFLOW_API_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
type Device = { token: string; clinicId: string; deviceId: string };
let cached: { value: Device; until: number } | null = null;

/** Explicitly enabled installation provisioning. Credentials never enter a browser bundle. */
export async function configuredDevice(): Promise<Device | null> {
  if (process.env.CAREFLOW_KIOSK_AUTO_SETUP !== "1") return null;
  const secret = process.env.CAREFLOW_KIOSK_DEVICE_SECRET ?? process.env.CAREFLOW_DEV_DEVICE_SECRET;
  if (!secret) return null;
  if (cached && cached.until > Date.now()) return cached.value;
  const deviceId = process.env.CAREFLOW_KIOSK_DEVICE_ID ?? seededKioskId();
  const response = await fetch(`${serverApiUrl()}/v1/auth/device-token`, {
    method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store",
    body: JSON.stringify({ device_id: deviceId, device_secret: secret, device_type: "kiosk" }),
    signal: AbortSignal.timeout(8_000),
  });
  if (!response.ok) return null;
  const data = await response.json() as { access_token: string; clinic_id: string };
  const value = { token: data.access_token, clinicId: data.clinic_id, deviceId };
  cached = { value, until: Date.now() + 45_000 };
  return value;
}
