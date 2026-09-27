import { hasConsent } from "@/consent/hasConsent";
import { z } from "zod";

import type { VoiceClient, VoiceEvents, VoiceSession } from "./index";

/**
 * Browser audio to the voice service over a plain WebSocket (no WebRTC in M1):
 * getUserMedia (echo cancellation, noise suppression) → the pcm16-capture AudioWorklet, which
 * sends 20 ms frames of 16 kHz PCM16 → `WS /kiosk/audio`. The question comes back as PCM16 at
 * 24 kHz and plays through an AudioContext queue. Nothing is recorded or stored: frames go
 * straight to the socket, and stop()/abort() stop every track and close both audio contexts.
 */
export const CAPTURE_WORKLET_URL = "/worklets/pcm16-capture.js";
export const INPUT_SAMPLE_RATE = 16000;
export const FRAME_SAMPLES = 320; // 20 ms at 16 kHz: 640-byte frames
export const OUTPUT_SAMPLE_RATE = 24000; // the question's PCM16 from the voice service
const OPEN = 1;

const questionSchema = z.object({
  question_id: z.string().min(1),
  text: z.string().min(1),
  key: z.string().nullable(),
  notice: z.string().nullable().optional(),
  // What the notice is (M2-10): `ask_on_phone` highlights "Continue on your phone".
  notice_kind: z
    .enum(["nurse_coming", "medical_refusal", "clinic_answer", "ask_on_phone", "finish_first"])
    .nullable()
    .optional(),
});

const serverMessage = z.discriminatedUnion("type", [
  z.object({ type: z.enum(["partial", "final"]), text: z.string() }),
  z.object({ type: z.literal("question"), text: z.string(), question: questionSchema.nullable(), done: z.boolean() }),
  z.object({ type: z.literal("tts_start"), sample_rate: z.number().int().positive() }),
  z.object({ type: z.literal("tts_end"), interrupted: z.boolean() }),
  z.object({ type: z.literal("error"), code: z.string() }),
]);

/** Browser APIs, injectable so the client can be tested without audio hardware. */
export interface VoiceDeps {
  getUserMedia(constraints: MediaStreamConstraints): Promise<MediaStream>;
  createAudioContext(options?: AudioContextOptions): AudioContext;
  createWorkletNode(context: AudioContext, name: string, options: AudioWorkletNodeOptions): AudioWorkletNode;
  createSocket(url: string): WebSocket;
}

export function browserDeps(): VoiceDeps {
  return {
    getUserMedia: (constraints) => navigator.mediaDevices.getUserMedia(constraints),
    createAudioContext: (options) => new AudioContext(options),
    createWorkletNode: (context, name, options) => new AudioWorkletNode(context, name, options),
    createSocket: (url) => new WebSocket(url),
  };
}

/** Plays PCM16 chunks back to back; flush() drops whatever is queued (barge-in, stop). */
class Player {
  private context: AudioContext | null = null;
  private next = 0;
  private readonly sources = new Set<AudioBufferSourceNode>();

  constructor(private readonly deps: VoiceDeps) {}

  /** Create (or keep) the playback context; call it inside the tap so autoplay allows it. */
  begin(sampleRate: number): void {
    if (this.context && this.context.sampleRate === sampleRate) return;
    void this.context?.close();
    this.context = this.deps.createAudioContext({ sampleRate });
    void this.context.resume();
    this.next = 0;
  }

  enqueue(pcm: ArrayBuffer): void {
    const context = this.context;
    if (!context || pcm.byteLength < 2) return;
    const samples = new Int16Array(pcm, 0, Math.floor(pcm.byteLength / 2));
    const buffer = context.createBuffer(1, samples.length, context.sampleRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i += 1) channel[i] = (samples[i] ?? 0) / 32768;
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    const at = Math.max(context.currentTime, this.next);
    source.start(at);
    this.next = at + buffer.duration;
    this.sources.add(source);
    source.onended = () => this.sources.delete(source);
  }

  flush(): void {
    for (const source of this.sources) {
      try {
        source.stop();
      } catch {
        // already stopped
      }
    }
    this.sources.clear();
    this.next = 0;
  }

  close(): void {
    this.flush();
    void this.context?.close();
    this.context = null;
  }
}

export class WsVoiceClient implements VoiceClient {
  readonly available = true;
  private socket: WebSocket | null = null;
  private stream: MediaStream | null = null;
  private capture: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private events: VoiceEvents | null = null;
  private readonly player: Player;
  private generation = 0;
  private settleOpen: (() => void) | null = null;

  constructor(
    private readonly url: string,
    private readonly deps: VoiceDeps = browserDeps(),
  ) {
    this.player = new Player(deps);
  }

  async start(session: VoiceSession, events: VoiceEvents): Promise<void> {
    // The microphone never opens without the patient's voice consent (M2-05).
    if (!hasConsent("voice")) throw new Error("Voice was not consented to.");
    this.release();
    const generation = (this.generation += 1);
    this.events = events;
    try {
      await this.open(session, () => generation === this.generation);
    } catch (error) {
      if (generation === this.generation) this.release(); // never leave the microphone open
      throw error;
    }
  }

  private async open(session: VoiceSession, current: () => boolean): Promise<void> {
    // Both audio contexts are created before the first await, inside the patient's tap: a
    // context created later may start suspended under the browser's autoplay policy.
    const capture = this.deps.createAudioContext();
    this.capture = capture;
    void capture.resume();
    this.player.begin(OUTPUT_SAMPLE_RATE);
    const stream = await this.deps.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
    });
    if (!current()) return stopTracks(stream); // stopped while the permission prompt was open
    this.stream = stream;
    await capture.audioWorklet.addModule(CAPTURE_WORKLET_URL);
    if (!current()) return;
    const node = this.deps.createWorkletNode(capture, "pcm16-capture", {
      processorOptions: { targetRate: INPUT_SAMPLE_RATE, frameSamples: FRAME_SAMPLES },
    });
    this.node = node;
    const mute = capture.createGain(); // keeps the graph running without playing the mic back
    mute.gain.value = 0;
    capture.createMediaStreamSource(stream).connect(node);
    node.connect(mute);
    mute.connect(capture.destination);

    const socket = this.deps.createSocket(this.url);
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    await new Promise<void>((resolve, reject) => {
      this.settleOpen = resolve; // release() settles a pending open
      socket.onopen = () => {
        this.settleOpen = null;
        socket.send(
          JSON.stringify({
            type: "start",
            session_id: session.sessionId,
            token: session.token,
            sample_rate: INPUT_SAMPLE_RATE,
            language: session.language,
          }),
        );
        resolve();
      };
      socket.onerror = () => {
        this.settleOpen = null;
        reject(new Error("The voice service is unreachable."));
      };
    });
    if (!current()) return;
    node.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
      if (socket.readyState === OPEN) socket.send(event.data);
    };
    socket.onmessage = (event: MessageEvent<unknown>) => this.receive(event.data);
    socket.onclose = (event: CloseEvent) => {
      if (!current()) return;
      this.events?.onError(event.code === 1008 ? "unauthorized" : "disconnected");
      this.release();
    };
  }

  stop(): void {
    if (this.socket?.readyState === OPEN) this.socket.send(JSON.stringify({ type: "stop" }));
    this.release();
  }

  abort(): void {
    this.events = null;
    this.release();
  }

  private receive(data: unknown): void {
    if (data instanceof ArrayBuffer) {
      this.player.enqueue(data);
      return;
    }
    if (typeof data !== "string") return;
    let parsed: z.infer<typeof serverMessage>;
    try {
      parsed = serverMessage.parse(JSON.parse(data));
    } catch {
      return; // not a message this client understands
    }
    switch (parsed.type) {
      case "final":
        this.events?.onTranscript(parsed.text);
        break;
      case "question":
        this.events?.onQuestion(parsed.question ? { ...parsed.question, notice: parsed.question.notice ?? null } : null);
        break;
      case "tts_start":
        this.player.begin(parsed.sample_rate);
        break;
      case "tts_end":
        if (parsed.interrupted) this.player.flush();
        break;
      case "error":
        this.events?.onError(parsed.code);
        break;
      default:
        break;
    }
  }

  private release(): void {
    this.generation += 1;
    const settle = this.settleOpen;
    this.settleOpen = null;
    settle?.();
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      socket.onopen = null;
      socket.onmessage = null;
      socket.onclose = null;
      socket.onerror = null;
      try {
        socket.close(1000);
      } catch {
        // already closed
      }
    }
    if (this.node) {
      this.node.port.onmessage = null;
      this.node.disconnect();
      this.node = null;
    }
    if (this.stream) stopTracks(this.stream);
    this.stream = null;
    void this.capture?.close();
    this.capture = null;
    this.player.close();
  }
}

function stopTracks(stream: MediaStream): void {
  for (const track of stream.getTracks()) track.stop();
}
