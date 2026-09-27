import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { Schema } from "@careflow/contracts";
import { act, render, renderHook, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NurseSession } from "@/components/nurse/NurseSession";
import { MESSAGES } from "@/i18n/messages";
import { type WorklistItem } from "@/lib/sortWorklist";
import { useSessionAlerts } from "@/lib/useSessionAlerts";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api", () => ({ API_BASE_URL: "http://api.test", api: { GET: get, POST: post } }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/nurse/ses_e2e",
  useSearchParams: () => new URLSearchParams(),
}));

type TriageView = Schema<"TriageView">;
type Alert = Schema<"Alert">;
const fixture = <T,>(name: string): T => JSON.parse(readFileSync(resolve(process.cwd(), `tests/fixtures/api/${name}.json`), "utf8")) as T;
const TRIAGE = fixture<TriageView>("triage");
const FACTS = fixture<Schema<"StructuredFacts">>("facts");
const ME = fixture<Schema<"Me">>("me");
const SESSION = TRIAGE.session_id;
const DECIDED_AT = "2026-09-26T09:20:00Z";
const ECG = TRIAGE.alerts[0]!; // raised 09:00, before the decision
const FAST: Alert = {
  ...ECG,
  alert_id: "alr_fast_after",
  rule_id: "FAST_STROKE",
  severity: "critical",
  evidence_fact_ids: [],
  display_text: "Possible stroke: FAST positive",
  raised_at: "2026-09-26T09:40:00Z", // after the decision: "I feel worse" with FAST signs
  deadline_at: undefined,
};
const DECISION: Schema<"NurseDecision"> = {
  decision_id: "dec_000000000001",
  session_id: SESSION,
  suggestion_id: null,
  action: "OVERRIDE",
  override_reason_code: "clinical_judgement",
  final_level: 2,
  scale: "KTAS",
  nurse_user_id: ME.user_id,
  acknowledged_alert_ids: [],
  decided_at: DECIDED_AT,
};
const confirmed = (alerts: Alert[], extra: Partial<TriageView> = {}): TriageView => ({
  ...TRIAGE,
  status: "confirmed",
  decision: DECISION,
  alerts,
  ...extra,
});
const reply = (data: unknown, status = 200) => ({ data, response: { status } });

class FakeSocket {
  static all: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<unknown>) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(readonly url: string) {
    FakeSocket.all.push(this);
  }
  open() {
    this.onopen?.();
  }
  send(frame: object) {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent<unknown>);
  }
  drop() {
    this.onclose?.();
  }
  close() {}
}

let triage: TriageView;
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
  triage = confirmed([ECG]);
  get.mockReset();
  post.mockReset();
  get.mockImplementation(async (path: string) => {
    if (path === "/v1/sessions/{session_id}/triage") return reply(triage);
    if (path === "/v1/sessions/{session_id}/facts") return reply(FACTS);
    return reply(ME);
  });
  post.mockResolvedValue(reply({ ticket: "t-1", channel: "staff", expires_at: "2026-09-26T09:00:30Z" }));
  FakeSocket.all = [];
  vi.stubGlobal("WebSocket", FakeSocket);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function settle(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}
const triageReads = () => get.mock.calls.filter(([path]) => path === "/v1/sessions/{session_id}/triage").length;

function show() {
  return render(
    <NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC">
      <NurseSession sessionId={SESSION} />
    </NextIntlClientProvider>,
  );
}

describe("the nurse's session card after the decision (M2-07)", () => {
  it("keeps every alert on screen, and marks the ones raised after the decision", async () => {
    triage = confirmed([ECG, FAST]);
    show();
    await settle();
    const banners = screen.getAllByTestId("alert-banner");
    expect(banners).toHaveLength(2);
    const marked = screen.getAllByTestId("after-decision");
    expect(marked).toHaveLength(1);
    expect(banners.find((banner) => banner.contains(marked[0]!))).toHaveTextContent("Possible stroke");
  });

  it("shows the patient's \"I feel worse\" presses", async () => {
    triage = confirmed([ECG], {
      feels_worse: { press_count: 2, last_reported_at: "2026-09-26T09:41:00Z", danger_answer_codes: ["face_droop"] },
    });
    show();
    await settle();
    expect(screen.getByTestId("feels-worse-banner")).toHaveTextContent("Pressed \"I feel worse\"");
    expect(screen.getByTestId("feels-worse-count")).toHaveTextContent("2");
  });

  it("reloads when this session's alert arrives live, and not for another session's", async () => {
    show();
    await settle();
    expect(screen.getAllByTestId("alert-banner")).toHaveLength(1);
    act(() => FakeSocket.all[0]!.open());
    await settle(200); // the snapshot on "live"
    const reads = triageReads();

    act(() => FakeSocket.all[0]!.send({ id: "1-0", type: "alert.raised", data: { session_id: "another-session" } }));
    await settle(200);
    expect(triageReads()).toBe(reads);

    triage = confirmed([ECG, FAST]);
    act(() => FakeSocket.all[0]!.send({ id: "2-0", type: "alert.raised", data: { session_id: SESSION } }));
    await settle(200);
    expect(triageReads()).toBe(reads + 1);
    expect(screen.getAllByTestId("alert-banner")).toHaveLength(2);
    expect(screen.getByTestId("after-decision")).toBeInTheDocument();
  });

  it("keeps the newer reload when an older, slower one answers last (SHE-325)", async () => {
    show();
    await settle();
    act(() => FakeSocket.all[0]!.open());
    await settle(200);
    const held: ((value: unknown) => void)[] = [];
    get.mockImplementation(async (path: string) => {
      if (path === "/v1/sessions/{session_id}/triage") return new Promise((answer) => held.push(answer));
      if (path === "/v1/sessions/{session_id}/facts") return reply(FACTS);
      return reply(ME);
    });
    act(() => FakeSocket.all[0]!.send({ id: "4-0", type: "alert.raised", data: { session_id: SESSION } }));
    await settle(200); // reload 1, still waiting for its answer
    act(() => FakeSocket.all[0]!.send({ id: "5-0", type: "patient.feels_worse", data: { session_id: SESSION } }));
    await settle(200); // reload 2
    expect(held).toHaveLength(2);

    await act(async () => held[1]!(reply(confirmed([ECG, FAST]))));
    await settle();
    expect(screen.getAllByTestId("alert-banner")).toHaveLength(2);
    await act(async () => held[0]!(reply(confirmed([ECG])))); // the older answer arrives last
    await settle();
    expect(screen.getAllByTestId("alert-banner")).toHaveLength(2); // still the newer card
  });

  it("takes a duplicated frame once: one reload of the server's state, nothing counted twice", async () => {
    triage = confirmed([ECG], {
      feels_worse: { press_count: 1, last_reported_at: "2026-09-26T09:41:00Z", danger_answer_codes: [] },
    });
    show();
    await settle();
    act(() => FakeSocket.all[0]!.open());
    await settle(200);
    const reads = triageReads();
    const frame = { id: "3-0", type: "patient.feels_worse", data: { session_id: SESSION } };
    act(() => {
      FakeSocket.all[0]!.send(frame);
      FakeSocket.all[0]!.send(frame); // delivered twice (a replay after a reconnect)
    });
    await settle(200);
    expect(triageReads()).toBe(reads + 1);
    expect(screen.getByTestId("feels-worse-count")).toHaveTextContent("1"); // the server's count
  });

  it("reloads the snapshot after a reconnection, and polls while the channel is down", async () => {
    show();
    await settle();
    act(() => FakeSocket.all[0]!.open());
    await settle(200);
    const reads = triageReads();

    triage = confirmed([ECG, FAST]);
    act(() => FakeSocket.all[0]!.drop());
    await settle(0);
    expect(screen.getByTestId("live-status")).toHaveTextContent("paused");
    await settle(5_000); // paused: the 5 s poll (the reconnect is also due after 1 s)
    expect(triageReads()).toBeGreaterThan(reads);
    expect(screen.getAllByTestId("alert-banner")).toHaveLength(2);

    const before = triageReads();
    const next = FakeSocket.all.at(-1)!;
    act(() => next.open());
    await settle(200);
    expect(triageReads()).toBe(before + 1);
    expect(post.mock.calls.filter(([path]) => path === "/v1/ws-ticket").length).toBeGreaterThanOrEqual(2);
  });

  it("keeps the last card when a reload fails", async () => {
    triage = confirmed([ECG, FAST]);
    show();
    await settle();
    get.mockResolvedValue(reply(undefined, 503));
    act(() => FakeSocket.all[0]!.open());
    await settle(200);
    expect(screen.getAllByTestId("alert-banner")).toHaveLength(2);
  });
});

describe("the worklist's alert banners after the decision (M2-07)", () => {
  const item = (alerts: WorklistItem["alerts"]): WorklistItem => ({
    session_id: SESSION,
    patient_display: "M. S.",
    arrived_at: "2026-09-26T08:55:00Z",
    state: "queued",
    alerts,
    confirmed_level: 2,
    decided_at: DECIDED_AT,
    open_tasks: [],
  });
  const chip = (alert: Alert) => ({
    alert_id: alert.alert_id,
    rule_id: alert.rule_id,
    severity: alert.severity,
    partial: alert.partial,
    raised_at: alert.raised_at,
  });

  it("show only an alert raised after the decision, marked with the decision", async () => {
    triage = confirmed([ECG, FAST]);
    const { result } = renderHook(() => useSessionAlerts([item([chip(ECG), chip(FAST)])]));
    await settle();
    expect(result.current.sessions).toHaveLength(1);
    expect(result.current.sessions[0]!.decidedAt).toBe(DECIDED_AT);
    expect(result.current.sessions[0]!.alerts.map((alert) => alert.alert_id)).toEqual([FAST.alert_id]);
  });

  it("stay away for a confirmed patient whose alerts all came before the decision", async () => {
    const { result } = renderHook(() => useSessionAlerts([item([chip(ECG)])]));
    await settle();
    expect(result.current.sessions).toEqual([]);
    expect(triageReads()).toBe(0);
  });
});
