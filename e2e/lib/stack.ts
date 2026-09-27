/**
 * The running stack the journeys drive (M1-19): its URLs from `.run/stacks/<CAREFLOW_STACK>.env`
 * (scripts/stack.py; the dev stack `careflow` by default) and the dev credentials `seed-dev`
 * created, read from `.env` (never printed). The dev clinic, staff and device ids are the
 * `uuid5` names `careflow-api seed-dev` gives them.
 */
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";

const ROOT = new URL("../../", import.meta.url).pathname;
/** careflow_api.cli.NAMESPACE */
const DEV_NAMESPACE = "7b0f1f7e-5c1a-4c4e-9f3e-2f8a8f0c0a55";

function parseEnv(path: string): Record<string, string> {
  if (!existsSync(path)) return {};
  const values: Record<string, string> = {};
  for (const raw of readFileSync(path, "utf8").split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const at = line.indexOf("=");
    let value = line.slice(at + 1).trim();
    if (value.length >= 2 && value[0] === value.at(-1) && (value[0] === "'" || value[0] === '"')) value = value.slice(1, -1);
    values[line.slice(0, at).trim()] = value;
  }
  return values;
}

const stackName = process.env.CAREFLOW_STACK ?? "careflow";
const stackEnv = parseEnv(`${ROOT}.run/stacks/${stackName}.env`);
const dotEnv = parseEnv(`${ROOT}.env`);

function setting(key: string, fallback: string): string {
  return process.env[key] ?? stackEnv[key] ?? fallback;
}

export const STACK = {
  name: stackName,
  api: setting("CAREFLOW_API_URL", "http://localhost:8000"),
  kiosk: `http://localhost:${setting("CAREFLOW_KIOSK_PORT", "3000")}`,
  staff: `http://localhost:${setting("CAREFLOW_STAFF_PORT", "3001")}`,
  fhir: setting("FHIR_BASE_URL", "http://localhost:8080/fhir").replace(/\/+$/, ""),
} as const;

/** A dev secret from the environment or `.env`; the journeys never print one. */
export function secret(key: "CAREFLOW_DEV_PASSWORD" | "CAREFLOW_DEV_DEVICE_SECRET"): string {
  const value = process.env[key] ?? dotEnv[key];
  if (!value) throw new Error(`${key} is not set: run python3 scripts/gen_dev_env.py, then make up`);
  return value;
}

/** Python's uuid.uuid5(NAMESPACE, "/".join(parts)). */
export function devId(...parts: string[]): string {
  const namespace = Buffer.from(DEV_NAMESPACE.replace(/-/g, ""), "hex");
  const hash = createHash("sha1").update(Buffer.concat([namespace, Buffer.from(parts.join("/"), "utf8")])).digest();
  const bytes = Buffer.from(hash.subarray(0, 16));
  bytes[6] = (bytes[6]! & 0x0f) | 0x50;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = bytes.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export const CLINIC_A = devId("clinic-a");
export const KIOSK_A = devId("clinic-a", "kiosk");
export const BOARD_A = devId("clinic-a", "board");
export const staffUser = (role: "nurse" | "doctor" | "admin") => `${role}.a@careflow.example`;
