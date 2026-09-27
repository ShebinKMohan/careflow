import { beforeEach, describe, expect, it, vi } from "vitest";

import type { VoiceEvents } from "@/lib/voice";
import { type VoiceDeps, WsVoiceClient } from "@/lib/voice/wsVoiceClient";

const URL = "ws://voice.test/kiosk/audio";
const SESSION = { sessionId: "ses_e2e", token: "example-session-token-e2e", language: "en" as const };

class FakeSocket {
  binaryType = "blob";
  readyState = 0;
  sent: (string | ArrayBuffer)[] = [];
  closedWith: number | null = null;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(readonly url: string) {}
  send(data: string | ArrayBuffer): void {
    this.sent.push(data);
  }
  close(code = 1000): void {
    this.readyState = 3;
    this.closedWith = code;
  }
  open(): void {
    this.readyState = 1;
    this.onopen?.();
  }
  serverSends(data: unknown): void {
    this.onmessage?.({ data });
  }
  json(): unknown[] {
    return this.sent.filter((item): item is string => typeof item === "string").map((item) => JSON.parse(item));
  }
}

class FakeSource {
  buffer: unknown = null;
  onended: (() => void) | null = null;
  startedAt: number | null = null;
  stopped = false;
  connect = vi.fn();
  start(at: number): void {
    this.startedAt = at;
  }
  stop(): void {
    this.stopped = true;
  }
}

class FakeContext {
  readonly sampleRate: number;
  currentTime = 0;
  closed = false;
  destination = {};
  sources: FakeSource[] = [];
  audioWorklet = { addModule: vi.fn(async () => {}) };
  resume = vi.fn(async () => {});

  constructor(options?: AudioContextOptions) {
    this.sampleRate = options?.sampleRate ?? 48000;
  }
  createGain() {
    return { gain: { value: 1 }, connect: vi.fn() };
  }
  createMediaStreamSource() {
    return { connect: vi.fn() };
  }
  createBuffer(_channels: number, length: number, rate: number) {
    const data = new Float32Array(length);
    return { duration: length / rate, getChannelData: () => data, data };
  }
  createBufferSource(): FakeSource {
    const source = new FakeSource();
    this.sources.push(source);
    return source;
  }
  async close(): Promise<void> {
    this.closed = true;
  }
}

class FakeNode {
  port: { onmessage: ((event: { data: ArrayBuffer }) => void) | null } = { onmessage: null };
  connect = vi.fn();
  disconnect = vi.fn();
  constructor(readonly options: AudioWorkletNodeOptions) {}
}

function setup() {
  const track = { stopped: false, stop() { this.stopped = true; } };
  const stream = { getTracks: () => [track] };
  const sockets: FakeSocket[] = [];
  const contexts: FakeContext[] = [];
  const nodes: FakeNode[] = [];
  let grant: (() => void) | null = null;
  const deps = {
    getUserMedia: vi.fn(
      () =>
        new Promise<MediaStream>((resolve) => {
          grant = () => resolve(stream as unknown as MediaStream);
        }),
    ),
    createAudioContext: (options?: AudioContextOptions) => {
      const context = new FakeContext(options);
      contexts.push(context);
      return context as unknown as AudioContext;
    },
    createWorkletNode: (_context: AudioContext, _name: string, options: AudioWorkletNodeOptions) => {
      const node = new FakeNode(options);
      nodes.push(node);
      return node as unknown as AudioWorkletNode;
    },
    createSocket: (url: string) => {
      const socket = new FakeSocket(url);
      sockets.push(socket);
      return socket as unknown as WebSocket;
    },
  } satisfies VoiceDeps;
  const events: VoiceEvents = { onTranscript: vi.fn(), onQuestion: vi.fn(), onError: vi.fn() };
  const client = new WsVoiceClient(URL, deps);
  const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
  async function connect() {
    const started = client.start(SESSION, events);
    await flush();
    grant?.();
    await flush();
    sockets[0]?.open();
    await started;
    return sockets[0]!;
  }
  return { client, deps, events, track, sockets, contexts, nodes, connect, flush, grant: () => grant?.() };
}

describe("WsVoiceClient", () => {
  let t: ReturnType<typeof setup>;
  beforeEach(() => {
    t = setup();
  });

  it("opens the microphone with echo cancellation and sends the token only in the start message", async () => {
    const socket = await t.connect();
    expect(t.deps.getUserMedia).toHaveBeenCalledWith({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
    });
    expect(t.contexts[0]!.audioWorklet.addModule).toHaveBeenCalledWith("/worklets/pcm16-capture.js");
    expect(t.nodes[0]!.options.processorOptions).toEqual({ targetRate: 16000, frameSamples: 320 });
    expect(socket.url).toBe(URL);
    expect(socket.binaryType).toBe("arraybuffer");
    expect(socket.json()).toEqual([
      { type: "start", session_id: "ses_e2e", token: "example-session-token-e2e", sample_rate: 16000, language: "en" },
    ]);
  });

  it("sends the worklet's 20 ms frames to the voice service as they come", async () => {
    const socket = await t.connect();
    const frame = new Int16Array(320).buffer;
    t.nodes[0]!.port.onmessage?.({ data: frame });
    expect(socket.sent.at(-1)).toBe(frame);
  });

  it("reports the transcript, the next question and errors, and ignores anything else", async () => {
    const socket = await t.connect();
    socket.serverSends(JSON.stringify({ type: "final", text: "I have chest pain since this morning." }));
    const question = { question_id: "q_pain_score_1", text: "How bad is the pain?", key: "pain_score" };
    socket.serverSends(JSON.stringify({ type: "question", text: "How bad is the pain?", question, done: false }));
    socket.serverSends(JSON.stringify({ type: "question", text: "Thank you.", question: null, done: true }));
    socket.serverSends(JSON.stringify({ type: "error", code: "api_unavailable" }));
    socket.serverSends("not json");
    socket.serverSends(JSON.stringify({ type: "question", text: "x", question: { text: "no id" }, done: false }));
    expect(t.events.onTranscript).toHaveBeenCalledExactlyOnceWith("I have chest pain since this morning.");
    expect(t.events.onQuestion).toHaveBeenNthCalledWith(1, { ...question, notice: null });
    expect(t.events.onQuestion).toHaveBeenNthCalledWith(2, null);
    expect(t.events.onQuestion).toHaveBeenCalledTimes(2);
    expect(t.events.onError).toHaveBeenCalledExactlyOnceWith("api_unavailable");
  });

  it("plays the question at 24 kHz back to back and drops it on a barge-in", async () => {
    const socket = await t.connect();
    socket.serverSends(JSON.stringify({ type: "tts_start", sample_rate: 24000 }));
    socket.serverSends(new Int16Array([0, 16384, -16384, 32767]).buffer);
    socket.serverSends(new Int16Array(2400).buffer);
    const playback = t.contexts[1]!;
    expect(playback.sampleRate).toBe(24000);
    const [first, second] = playback.sources;
    expect(first!.startedAt).toBe(0);
    expect(second!.startedAt).toBeCloseTo(4 / 24000);
    expect(Array.from((first!.buffer as { data: Float32Array }).data)).toEqual([0, 0.5, -0.5, 32767 / 32768]);
    socket.serverSends(JSON.stringify({ type: "tts_end", interrupted: true }));
    expect(playback.sources.every((source) => source.stopped)).toBe(true);
  });

  it("stop() sends stop, closes the socket, stops the microphone and closes both audio contexts", async () => {
    const socket = await t.connect();
    socket.serverSends(JSON.stringify({ type: "tts_start", sample_rate: 24000 }));
    t.client.stop();
    expect(socket.json().at(-1)).toEqual({ type: "stop" });
    expect(socket.closedWith).toBe(1000);
    expect(t.track.stopped).toBe(true);
    expect(t.contexts.every((context) => context.closed)).toBe(true);
  });

  it("abort() delivers nothing more and releases everything", async () => {
    const socket = await t.connect();
    const onmessage = socket.onmessage;
    t.client.abort();
    onmessage?.({ data: JSON.stringify({ type: "final", text: "late" }) });
    expect(t.events.onTranscript).not.toHaveBeenCalled();
    expect(t.track.stopped).toBe(true);
    expect(socket.closedWith).toBe(1000);
  });

  it("a 1008 close (bad token) is reported as unauthorized and frees the microphone", async () => {
    const socket = await t.connect();
    socket.onclose?.({ code: 1008 });
    expect(t.events.onError).toHaveBeenCalledExactlyOnceWith("unauthorized");
    expect(t.track.stopped).toBe(true);
  });

  it("stopping while the permission prompt is open releases the microphone once granted", async () => {
    const started = t.client.start(SESSION, t.events);
    await t.flush();
    t.client.stop();
    t.grant();
    await started;
    expect(t.track.stopped).toBe(true);
    expect(t.sockets).toHaveLength(0);
  });

  it("an unreachable voice service rejects start() and releases the microphone", async () => {
    const started = t.client.start(SESSION, t.events);
    await t.flush();
    t.grant();
    await t.flush();
    t.sockets[0]!.onerror?.();
    await expect(started).rejects.toThrow("unreachable");
    expect(t.track.stopped).toBe(true);
    expect(t.contexts[0]!.closed).toBe(true);
  });
});
