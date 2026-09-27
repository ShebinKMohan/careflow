/**
 * The kiosk's voice input. M1-14 implements a real client (browser audio to the voice agent);
 * until then `NoopVoiceClient` is used, and the mic button shows only when
 * NEXT_PUBLIC_VOICE_ENABLED=true. Voice is never used on the keypad screen.
 */
export interface VoiceClient {
  /** True when this client can capture speech. */
  readonly available: boolean;
  /** Listen until `stop()`; `onText` gets the final transcript. */
  start(onText: (text: string) => void): Promise<void>;
  stop(): void;
  /** Drop everything now (idle wipe): no transcript is delivered and nothing is kept. */
  abort(): void;
}

export class NoopVoiceClient implements VoiceClient {
  readonly available = false;
  async start(): Promise<void> {}
  stop(): void {}
  abort(): void {}
}

export function voiceEnabled(): boolean {
  return process.env.NEXT_PUBLIC_VOICE_ENABLED === "true";
}

export function createVoiceClient(): VoiceClient {
  return new NoopVoiceClient();
}
