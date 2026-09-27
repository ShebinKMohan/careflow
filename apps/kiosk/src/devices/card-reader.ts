/**
 * The ID-card reader (M2-05). Only a mock exists: `MockCardReader` emits synthetic
 * "mock_national_id" payloads when the simulated reader panel is tapped. Never a real Emirates ID
 * or UAE Pass reader.
 */
export interface CardPayload {
  card_type: "mock_national_id";
  /** A synthetic Emirates ID-style number, 784-YYYY-NNNNNNN-C. */
  id_number: string;
}

export interface CardReader {
  /** Calls `cb` on every tap; returns the unsubscribe function. */
  onTap(cb: (payload: CardPayload) => void): () => void;
}

export class MockCardReader implements CardReader {
  private readonly listeners = new Set<(payload: CardPayload) => void>();

  onTap(cb: (payload: CardPayload) => void): () => void {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  }

  /** A simulated tap of a mock card. */
  tap(payload: CardPayload): void {
    for (const listener of this.listeners) listener(payload);
  }
}

/** The kiosk's one reader (a mock in this prototype). */
export const cardReader = new MockCardReader();

export function simulatorsEnabled(): boolean {
  return process.env.NEXT_PUBLIC_SIMULATORS === "1";
}
