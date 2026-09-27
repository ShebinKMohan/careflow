import { beforeAll, describe, expect, it } from "vitest";

// The shipped worklet (public/worklets/pcm16-capture.js), run with the AudioWorklet globals stubbed.
type Processor = { process(inputs: Float32Array[][]): boolean };
type ProcessorClass = new (options: { processorOptions: Record<string, number> }) => Processor;

const posted: ArrayBuffer[] = [];
let Pcm16Capture: ProcessorClass;
const scope = globalThis as unknown as Record<string, unknown>;

beforeAll(async () => {
  scope.AudioWorkletProcessor = class {
    port = { postMessage: (data: ArrayBuffer) => posted.push(data) };
  };
  scope.registerProcessor = (name: string, processor: ProcessorClass) => {
    if (name === "pcm16-capture") Pcm16Capture = processor;
  };
  // @ts-expect-error -- plain JavaScript, loaded for its registerProcessor() side effect
  await import("../public/worklets/pcm16-capture.js");
});

function run(rate: number, seconds: number, signal: (t: number) => number): Int16Array[] {
  posted.length = 0;
  scope.sampleRate = rate;
  const processor = new Pcm16Capture({ processorOptions: { targetRate: 16000, frameSamples: 320 } });
  const total = Math.round(rate * seconds);
  for (let start = 0; start < total; start += 128) {
    const block = new Float32Array(Math.min(128, total - start));
    for (let i = 0; i < block.length; i += 1) block[i] = signal((start + i) / rate);
    processor.process([[block]]);
  }
  return posted.map((buffer) => new Int16Array(buffer));
}

describe("pcm16-capture worklet", () => {
  it("turns 1 s at 48 kHz into fifty 20 ms frames of 16 kHz PCM16", () => {
    const frames = run(48000, 1, (t) => 0.5 * Math.sin(2 * Math.PI * 440 * t));
    expect(frames).toHaveLength(50);
    expect(frames.every((frame) => frame.length === 320 && frame.byteLength === 640)).toBe(true);
    const peak = Math.max(...frames.flatMap((frame) => Array.from(frame, Math.abs)));
    expect(peak).toBeGreaterThan(0.48 * 32767);
    expect(peak).toBeLessThanOrEqual(0.5 * 32767 + 1);
  });

  it("resamples 44.1 kHz too", () => {
    const frames = run(44100, 1, (t) => 0.25 * Math.sin(2 * Math.PI * 300 * t));
    expect(frames.length).toBeGreaterThanOrEqual(49);
    expect(frames.length).toBeLessThanOrEqual(50);
  });

  it("clips out-of-range input and keeps silence silent", () => {
    const loud = run(48000, 0.02, () => 2);
    expect(loud).toHaveLength(1);
    expect(Array.from(loud[0]!)).toEqual(new Array(320).fill(32767));
    const quiet = run(48000, 0.02, () => 0);
    expect(Array.from(quiet[0]!)).toEqual(new Array(320).fill(0));
  });
});
