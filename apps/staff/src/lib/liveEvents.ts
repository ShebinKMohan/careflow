import { z } from "zod";

/**
 * The nurse station's live channel (M1-11): a one-time ticket from `POST /v1/ws-ticket`, then
 * `/v1/ws/staff?ticket=…`. A dropped socket reports "paused" (the caller polls the REST API) and
 * reconnects with backoff, passing `last_event_id` so missed events are replayed.
 */
export type LiveStatus = "connecting" | "live" | "paused";

const frameSchema = z.object({
  id: z.string().optional(),
  type: z.string(),
  timestamp: z.string().optional(),
  data: z.unknown().optional(),
});
export type LiveFrame = z.infer<typeof frameSchema>;

export interface LiveDeps {
  /** A fresh one-time ticket, or null when the API refused or is unreachable. */
  requestTicket(): Promise<string | null>;
  createSocket(url: string): WebSocket;
  setTimeout(callback: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
}

export interface LiveHandlers {
  onFrame(frame: LiveFrame): void;
  onStatus(status: LiveStatus): void;
}

export const RECONNECT_MIN_MS = 1_000;
export const RECONNECT_MAX_MS = 30_000;

export class StaffLiveChannel {
  private socket: WebSocket | null = null;
  private timer: unknown = null;
  private attempts = 0;
  private lastEventId: string | null = null;
  private stopped = false;

  constructor(
    private readonly wsBase: string,
    private readonly deps: LiveDeps,
    private readonly handlers: LiveHandlers,
  ) {}

  start(): void {
    this.stopped = false;
    this.handlers.onStatus("connecting");
    void this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.timer !== null) this.deps.clearTimeout(this.timer);
    this.timer = null;
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      socket.onclose = null;
      socket.onmessage = null;
      socket.close(1000);
    }
  }

  private async connect(): Promise<void> {
    this.timer = null;
    const ticket = await this.deps.requestTicket();
    if (this.stopped) return;
    if (!ticket) {
      this.pause();
      return;
    }
    const query = new URLSearchParams({ ticket });
    if (this.lastEventId) query.set("last_event_id", this.lastEventId);
    const socket = this.deps.createSocket(`${this.wsBase}/v1/ws/staff?${query.toString()}`);
    this.socket = socket;
    socket.onopen = () => {
      this.attempts = 0;
      this.handlers.onStatus("live");
    };
    socket.onmessage = (event: MessageEvent<unknown>) => this.receive(event.data);
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.pause();
    };
  }

  private receive(data: unknown): void {
    if (typeof data !== "string") return;
    let frame: LiveFrame;
    try {
      frame = frameSchema.parse(JSON.parse(data));
    } catch {
      return;
    }
    if (frame.type === "heartbeat") return;
    if (frame.id) this.lastEventId = frame.id;
    this.handlers.onFrame(frame);
  }

  private pause(): void {
    if (this.stopped) return;
    this.handlers.onStatus("paused");
    const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_MIN_MS * 2 ** this.attempts);
    this.attempts += 1;
    this.timer = this.deps.setTimeout(() => void this.connect(), delay);
  }
}

/** http(s)://host → ws(s)://host, for the API's WebSocket routes. */
export function wsBaseUrl(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/^http/, "ws").replace(/\/+$/, "");
}
