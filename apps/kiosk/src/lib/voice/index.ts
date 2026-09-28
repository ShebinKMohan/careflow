import type { IntakeQuestion } from "@/lib/session";

import { WsVoiceClient } from "./wsVoiceClient";

/**
 * The kiosk's voice input (M1-14). The voice service (`WS /kiosk/audio`) hears the patient,
 * posts each answer to the intake API itself and speaks the next question back, so the kiosk
 * only shows what was heard and moves to the next question. The mic button shows only when
 * NEXT_PUBLIC_VOICE_ENABLED=true. Voice is never used on the keypad screen.
 */
export interface VoiceSession {
  sessionId: string;
  /** The patient_session token, sent in the first message (never in a URL). */
  token: string;
  language: "en" | "ar";
  inputMode?: "continuous" | "push_to_talk";
}

export interface VoiceEvents {
  /** What the voice service heard (the answer it sent to the intake API). */
  onTranscript(text: string): void;
  /** The next question after a spoken answer; null when the intake is done. */
  onQuestion(question: IntakeQuestion | null): void;
  /** The voice path failed; the patient can still tap or type. */
  onError(code: string): void;
}

export interface VoiceClient {
  /** True when this client can capture speech. */
  readonly available: boolean;
  /** Open the microphone and the voice service; resolves once audio is flowing. */
  start(session: VoiceSession, events: VoiceEvents): Promise<void>;
  /** End a held answer, stop microphone tracks, and keep the response socket open. */
  finishUtterance(): boolean;
  /** Stop listening and speaking (the patient taps the mic again or leaves the screen). */
  stop(): void;
  /** Drop everything now (idle wipe): no further events, no queued audio, tracks stopped. */
  abort(): void;
}

export class NoopVoiceClient implements VoiceClient {
  readonly available = false;
  async start(): Promise<void> {}
  finishUtterance(): boolean { return false; }
  stop(): void {}
  abort(): void {}
}

export const DEFAULT_VOICE_URL = "ws://localhost:8090/kiosk/audio";

export function voiceEnabled(): boolean {
  return process.env.NEXT_PUBLIC_VOICE_ENABLED === "true";
}

export function createVoiceClient(): VoiceClient {
  if (!voiceEnabled()) return new NoopVoiceClient();
  return new WsVoiceClient(process.env.NEXT_PUBLIC_VOICE_WS_URL ?? DEFAULT_VOICE_URL);
}
