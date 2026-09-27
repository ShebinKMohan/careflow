// The kiosk's urgency model (M3-02): the fine-tuned Laya (int8 ONNX, registry version from
// public/models/manifest.json) on onnxruntime-web WASM, in a Web Worker so the intake never waits.
//
// When the intake is done it reads the model input from the API (the server builds it: the kiosk
// has no copy of the fact serializer), runs the model, finds the reason keys by leaving one key out
// at a time (as the server model does, M3-01), and posts the five probabilities. The API decides
// what they mean. A 409 (the facts changed) re-reads and retries once; an idle reset aborts the
// run. Nothing the model says is ever rendered or logged here, and a failure leaves the server's
// suggestion in place.
import { Agent } from "@careflow/laya-ts";
import * as ort from "onnxruntime-web";

type Manifest = { laya: { version: string; path: string; files: Record<string, string> } };
type Questions = Record<string, { criteria: Record<string, string> } & Record<string, unknown>>;
type Loaded = { agent: Agent; version: string; questions: Questions };
type RunMessage = { type: "run"; apiBase: string; sessionId: string; token: string };
type Inbound = { type: "preload" } | RunMessage | { type: "abort" };
export type DeviceTriageStatus = "applied" | "stored" | "failed" | "aborted" | "unavailable";

const REASONS = 3;
const scope = self as unknown as {
  onmessage: ((event: MessageEvent<Inbound>) => void) | null;
  postMessage: (message: unknown) => void;
  crossOriginIsolated?: boolean;
};
let loading: Promise<Loaded> | null = null;
let generation = 0; // a new run or an abort makes older runs stop before they post

async function sha256Hex(data: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

function load(): Promise<Loaded> {
  if (!loading) {
    loading = (async () => {
      ort.env.wasm.wasmPaths = "/ort/";
      ort.env.wasm.numThreads = scope.crossOriginIsolated ? 4 : 1;
      const manifest = (await (await fetch("/models/manifest.json")).json()) as Manifest;
      const { version, path, files } = manifest.laya;
      const raw = await (await fetch(`${path}/question.json`)).arrayBuffer();
      if ((await sha256Hex(raw)) !== files["question.json"]) throw new Error("question.json does not match the manifest");
      const questions = (JSON.parse(new TextDecoder().decode(raw)) as { questions: Questions }).questions;
      const agent = await Agent.load(`${self.location.origin}${path}`, {
        expectedSha256: files,
        numThreads: scope.crossOriginIsolated ? 4 : 1,
        executionProviders: ["wasm"],
      });
      return { agent, version, questions };
    })();
    loading.catch(() => {
      loading = null; // try again next time
    });
  }
  return loading;
}

async function probabilities(loaded: Loaded, state: Record<string, string>): Promise<number[]> {
  const first = Object.entries(loaded.questions)[0];
  if (!first) throw new Error("question.json has no question");
  const [id, question] = first;
  const result = await loaded.agent.predict(state, loaded.questions as never);
  const answer = (result.answers as Record<string, { probabilities: Record<string, number> } | undefined>)[id];
  if (!answer) throw new Error("the model gave no answer");
  return Object.keys(question.criteria).map((key) => answer.probabilities[key] ?? 0);
}

async function reasonKeys(loaded: Loaded, state: Record<string, string>, full: number[]): Promise<string[]> {
  const best = full.indexOf(Math.max(...full));
  const scores: [string, number][] = [];
  for (const key of Object.keys(state)) {
    const without = Object.fromEntries(Object.entries(state).filter(([k]) => k !== key));
    const reduced = await probabilities(loaded, without);
    scores.push([key, (full[best] ?? 0) - (reduced[best] ?? 0)]);
  }
  const positive = scores.filter(([, score]) => score > 0).sort((a, b) => b[1] - a[1]);
  return positive.slice(0, REASONS).map(([key]) => key);
}

async function run(message: RunMessage, mine: number): Promise<DeviceTriageStatus> {
  const headers = { Authorization: `Bearer ${message.token}`, "Content-Type": "application/json" };
  const base = `${message.apiBase}/v1/sessions/${message.sessionId}`;
  let loaded: Loaded;
  try {
    loaded = await load();
  } catch {
    return "unavailable";
  }
  for (let attempt = 0; attempt < 2; attempt += 1) {
    if (mine !== generation) return "aborted";
    const read = await fetch(`${base}/model-input`, { headers });
    if (!read.ok) return "failed";
    const input = (await read.json()) as { state: Record<string, string>; facts_sha256: string };
    const started = performance.now();
    const full = await probabilities(loaded, input.state);
    const keys = await reasonKeys(loaded, input.state, full);
    const latency = Math.round(performance.now() - started);
    if (mine !== generation) return "aborted";
    const total = full.reduce((sum, p) => sum + p, 0);
    const posted = await fetch(`${base}/urgency-suggestion`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        model_family: "laya",
        model_version: loaded.version,
        facts_sha256: input.facts_sha256,
        probabilities: full.map((p) => p / total),
        reason_keys: keys,
        latency_ms: latency,
      }),
    });
    if (posted.status === 409 && attempt === 0) continue; // the facts changed: read again, once
    if (posted.status === 200) return "applied";
    return posted.status === 202 ? "stored" : "failed";
  }
  return "failed";
}

scope.onmessage = (event) => {
  const message = event.data;
  if (message.type === "preload") {
    void load().catch(() => undefined);
  } else if (message.type === "abort") {
    generation += 1;
  } else if (message.type === "run") {
    generation += 1;
    const mine = generation;
    void run(message, mine)
      .catch((): DeviceTriageStatus => "failed")
      .then((status) => scope.postMessage({ type: "done", status }));
  }
};
