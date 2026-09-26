/**
 * Browser-only session storage for the kiosk (tab-scoped, cleared when the tab closes).
 * The device sign-in is a stub for M0-08; real provisioning comes with the kiosk flows (M1-13).
 */
const DEVICE_KEY = "careflow.kiosk.device";
const PATIENT_KEY = "careflow.kiosk.patient-session";

export interface DeviceSession {
  token: string;
  clinicId: string;
  deviceId: string;
}

function read<T>(key: string): T | null {
  try {
    const raw = window.sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

export function readDevice(): DeviceSession | null {
  return read<DeviceSession>(DEVICE_KEY);
}

export function saveDevice(device: DeviceSession): void {
  window.sessionStorage.setItem(DEVICE_KEY, JSON.stringify(device));
}

/** The patient_session token from check-in, for the intake step. */
export function savePatientSession(session: { sessionId: string; token: string }): void {
  window.sessionStorage.setItem(PATIENT_KEY, JSON.stringify(session));
}
