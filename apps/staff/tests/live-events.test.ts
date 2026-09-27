import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type LiveStatus, StaffLiveChannel, wsBaseUrl } from "@/lib/liveEvents";

class FakeSocket {
  static all: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;
  constructor(readonly url: string) {
    FakeSocket.all.push(this);
  }
  close() {
    this.closed = true;
  }
  open() {
    this.onopen?.();
  }
  push(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
  drop() {
    this.onclose?.();
  }
}

function setup(ticket: () => string | null = () => "tkt") {
  const statuses: LiveStatus[] = [];
  const frames: unknown[] = [];
  const channel = new StaffLiveChannel(
    "ws://api.test",
    {
      requestTicket: async () => ticket(),
      createSocket: (url) => new FakeSocket(url) as unknown as WebSocket,
      setTimeout: (callback, ms) => setTimeout(callback, ms),
      clearTimeout: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
    },
    { onFrame: (frame) => frames.push(frame), onStatus: (status) => statuses.push(status) },
  );
  return { channel, statuses, frames };
}

describe("StaffLiveChannel", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeSocket.all = [];
  });
  afterEach(() => vi.useRealTimers());

  it("connects with a one-time ticket and passes frames on, skipping heartbeats", async () => {
    const { channel, statuses, frames } = setup();
    channel.start();
    await vi.advanceTimersByTimeAsync(0);
    const [socket] = FakeSocket.all;
    expect(socket!.url).toBe("ws://api.test/v1/ws/staff?ticket=tkt");
    socket!.open();
    socket!.push({ type: "heartbeat", timestamp: "t" });
    socket!.push({ id: "1-0", type: "alert.raised", timestamp: "t", data: { session_id: "s" } });
    socket!.push("not an envelope");
    expect(statuses).toEqual(["connecting", "live"]);
    expect(frames).toEqual([{ id: "1-0", type: "alert.raised", timestamp: "t", data: { session_id: "s" } }]);
    channel.stop();
  });

  it("a dropped socket pauses, then reconnects with backoff and the last event id", async () => {
    const { channel, statuses } = setup();
    channel.start();
    await vi.advanceTimersByTimeAsync(0);
    FakeSocket.all[0]!.open();
    FakeSocket.all[0]!.push({ id: "7-0", type: "token.called", data: {} });
    FakeSocket.all[0]!.drop();
    expect(statuses.at(-1)).toBe("paused");
    await vi.advanceTimersByTimeAsync(999);
    expect(FakeSocket.all).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeSocket.all[1]!.url).toBe("ws://api.test/v1/ws/staff?ticket=tkt&last_event_id=7-0");
    FakeSocket.all[1]!.drop(); // failed again before opening: the next wait doubles
    await vi.advanceTimersByTimeAsync(1_999);
    expect(FakeSocket.all).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeSocket.all).toHaveLength(3);
    channel.stop();
  });

  it("no ticket (API down or signed out) means paused and a later retry", async () => {
    let ticket: string | null = null;
    const { channel, statuses } = setup(() => ticket);
    channel.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(statuses).toEqual(["connecting", "paused"]);
    expect(FakeSocket.all).toHaveLength(0);
    ticket = "tkt";
    await vi.advanceTimersByTimeAsync(1_000);
    expect(FakeSocket.all).toHaveLength(1);
    channel.stop();
  });

  it("stop() closes the socket and cancels any retry", async () => {
    const { channel } = setup();
    channel.start();
    await vi.advanceTimersByTimeAsync(0);
    const socket = FakeSocket.all[0]!;
    channel.stop();
    expect(socket.closed).toBe(true);
    socket.drop();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakeSocket.all).toHaveLength(1);
  });

  it("derives the WebSocket base from the API base", () => {
    expect(wsBaseUrl("http://localhost:8000/")).toBe("ws://localhost:8000");
    expect(wsBaseUrl("https://api.example.test")).toBe("wss://api.example.test");
  });
});
