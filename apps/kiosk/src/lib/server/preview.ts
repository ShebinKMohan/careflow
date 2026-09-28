/** Server-derived presentation state; only the resulting boolean reaches the browser. */
export function isCloudPreview(): boolean {
  if (process.env.VERCEL !== "1") return false;
  const apiUrl = process.env.CAREFLOW_API_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL;
  const deviceSecret = process.env.CAREFLOW_KIOSK_DEVICE_SECRET ?? process.env.CAREFLOW_DEV_DEVICE_SECRET;
  return !apiUrl?.trim() || process.env.CAREFLOW_KIOSK_AUTO_SETUP !== "1" || !deviceSecret?.trim();
}
