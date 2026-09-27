import { act, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WaitingRoom } from "@/components/WaitingRoom";
import { MESSAGES } from "@/i18n/messages";
import { POLL_MS } from "@/lib/liveStatus";
import { saveSession, saveToken } from "@/lib/session";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api", () => ({
  API_BASE_URL: "http://api.test",
  api: { GET: get, POST: post },
  createCareflowClientFor: () => ({ GET: get, POST: post }),
}));

const STATUS = {
  state: "queued",
  position: 3,
  wait_estimate_minutes: 20,
  token: { token_id: "tok_1", session_id: "ses_phone", token_code: "G-031", department: "general", status: "waiting", issued_at: "2026-09-27T09:00:00Z" },
  room: null,
  patient_type: "adult",
};
const reply = (data: unknown, status = 200) => ({ data, response: { status } });

/** A WebSocket the test opens, closes and feeds by hand. */
class FakeSocket {
  static all: FakeSocket[] = [];
  static readonly OPEN = 1;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<unknown>) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(readonly url: string) {
    FakeSocket.all.push(this);
  }
  open() {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }
  drop() {
    this.readyState = 3;
    this.onclose?.();
  }
  close() {
    this.readyState = 3;
  }
}

function show(locale: "en" | "ar" = "en") {
  return render(
    <NextIntlClientProvider locale={locale} messages={MESSAGES[locale]} timeZone="UTC">
      <WaitingRoom />
    </NextIntlClientProvider>,
  );
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
  get.mockReset();
  post.mockReset();
  post.mockImplementation(async (path: string) => (path === "/v1/ws-ticket" ? reply({ ticket: "t-1" }) : reply(undefined, 500)));
  FakeSocket.all = [];
  vi.stubGlobal("WebSocket", FakeSocket);
  window.sessionStorage.clear();
  saveSession({ sessionId: "ses_phone", language: "en", display: "L. K." });
  saveToken("phone-token");
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function settle() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

describe("the waiting room's safety button", () => {
  it("stays, with the last good status, when a refresh fails", async () => {
    get.mockResolvedValueOnce(reply(STATUS)).mockResolvedValue(reply(undefined, 503));
    show();
    await settle();
    expect(screen.getByTestId("position")).toHaveTextContent("Position: 3");
    expect(screen.getByTestId("feels-worse")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS); // the socket never opened: the 15 s poll runs
    });
    expect(get).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("position")).toHaveTextContent("Position: 3");
    expect(screen.getByTestId("status-stale")).toHaveTextContent("last one we had");
    expect(screen.getByTestId("feels-worse")).toBeInTheDocument();
  });

  it("is there even when the status never loads", async () => {
    get.mockResolvedValue(reply(undefined, 503));
    show();
    await settle();
    expect(screen.getByRole("alert")).toHaveTextContent("could not be loaded");
    expect(screen.getByTestId("feels-worse")).toBeInTheDocument();
  });

  it("reloads the status whenever the live socket opens again", async () => {
    get.mockResolvedValueOnce(reply(STATUS)).mockResolvedValueOnce(reply({ ...STATUS, position: 2 }))
      .mockResolvedValue(reply({ ...STATUS, position: 1 }));
    show();
    await settle();
    expect(FakeSocket.all).toHaveLength(1);
    act(() => FakeSocket.all[0]!.open());
    await settle();
    expect(screen.getByTestId("position")).toHaveTextContent("Position: 2"); // the snapshot on open

    // The socket drops; the next one (fresh ticket, after the backoff) reloads the status again.
    act(() => FakeSocket.all[0]!.drop());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(FakeSocket.all).toHaveLength(2);
    act(() => FakeSocket.all[1]!.open());
    await settle();
    expect(screen.getByTestId("position")).toHaveTextContent("Position: 1");
    expect(post.mock.calls.filter(([path]) => path === "/v1/ws-ticket")).toHaveLength(2);
    expect(FakeSocket.all.every((socket) => !socket.url.includes("phone-token"))).toBe(true);
  });
});

describe("the end of the visit (M2-01)", () => {
  const closed = { ...STATUS, state: "closed", position: null, wait_estimate_minutes: null, token: { ...STATUS.token, status: "closed" } };

  it("says the visit has ended when the nurse's close arrives live, and keeps the safety button", async () => {
    get.mockResolvedValue(reply(STATUS));
    show();
    await settle();
    act(() => FakeSocket.all[0]!.open());
    await settle();
    expect(screen.queryByTestId("visit-ended")).not.toBeInTheDocument();

    act(() => FakeSocket.all[0]!.onmessage?.({ data: JSON.stringify({ type: "queue.status", data: closed }) } as MessageEvent<unknown>));
    expect(screen.getByTestId("visit-ended")).toHaveTextContent("This visit has ended. If you feel worse, tell the reception desk now.");
    expect(screen.queryByTestId("position")).not.toBeInTheDocument();
    expect(screen.getByTestId("feels-worse")).toBeInTheDocument();
  });

  it("sends a no-show to the reception desk, in the session's language", async () => {
    get.mockResolvedValue(reply({ ...closed, state: "no_show", token: { ...STATUS.token, status: "no_show" } }));
    show("ar");
    await settle();
    expect(screen.getByTestId("visit-ended")).toHaveTextContent(MESSAGES.ar.safety.status.missed);
  });
});
