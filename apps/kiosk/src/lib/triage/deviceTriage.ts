import { API_BASE_URL } from "@/lib/api";

import type { DeviceTriageStatus } from "./laya.worker";

/**
 * The kiosk urgency model (M3-02) on the main thread: one Web Worker per kiosk, preloaded when the
 * app starts (the int8 model is about 500 MB, cached by the browser). `runDeviceTriage` starts a
 * run when the intake is done; `abortDeviceTriage` stops it on an idle reset. Only a status comes
 * back (never the model's output), and the intake never waits for it: without a model, the
 * server's suggestion simply stays.
 */
let worker: Worker | null = null;
let listener: ((status: DeviceTriageStatus) => void) | null = null;

function layaWorker(): Worker | null {
  if (typeof window === "undefined" || typeof Worker === "undefined") return null;
  if (process.env.NEXT_PUBLIC_DEVICE_TRIAGE === "0") return null;
  if (!worker) {
    try {
      worker = new Worker(new URL("./laya.worker.ts", import.meta.url), { type: "module" });
      worker.onmessage = (event: MessageEvent<{ type: string; status: DeviceTriageStatus }>) => {
        if (event.data.type === "done") listener?.(event.data.status);
      };
    } catch {
      worker = null;
    }
  }
  return worker;
}

export function preloadDeviceTriage(): void {
  layaWorker()?.postMessage({ type: "preload" });
}

export function runDeviceTriage(sessionId: string, token: string): void {
  layaWorker()?.postMessage({ type: "run", apiBase: API_BASE_URL, sessionId, token });
}

export function abortDeviceTriage(): void {
  worker?.postMessage({ type: "abort" });
}

/** For tests and evidence runs: the status of each finished run. */
export function onDeviceTriageDone(callback: ((status: DeviceTriageStatus) => void) | null): void {
  listener = callback;
}
