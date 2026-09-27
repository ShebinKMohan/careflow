import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { WorklistItem } from "@/lib/sortWorklist";
import { useLiveWorklist } from "@/lib/useLiveWorklist";

// Overlapping reads (SHE-325): a live frame's refetch, the queue controls' reload and the poll can
// all be in flight at once. Only the newest read's answer may be shown: an older, slower answer
// put a closed patient's "Seen (free the room)" back on the nurse's worklist. The nurse's card
// is checked the same way in nurse-live.test.tsx.

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api", () => ({ API_BASE_URL: "http://api.test", api: { GET: get, POST: post } }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/nurse",
  useSearchParams: () => new URLSearchParams(),
}));

const fixture = <T,>(name: string): T => JSON.parse(readFileSync(resolve(process.cwd(), `tests/fixtures/api/${name}.json`), "utf8")) as T;
const reply = (data: unknown, status = 200) => ({ data, response: { status } });
const WORKLIST = fixture<WorklistItem[]>("worklist");

/** Every GET waits until the test answers it, in the order the test chooses. */
let pending: { path: string; answer: (value: unknown) => void }[];
beforeEach(() => {
  pending = [];
  get.mockReset();
  post.mockReset();
  get.mockImplementation((path: string) => new Promise((answer) => pending.push({ path, answer })));
  post.mockResolvedValue(reply({ ticket: "t-1", channel: "staff", expires_at: "2026-09-26T09:00:30Z" }));
  vi.stubGlobal("WebSocket", class { close() {} });
});
afterEach(() => vi.unstubAllGlobals());

async function answer(index: number, value: unknown) {
  await act(async () => {
    pending[index]!.answer(value);
    await Promise.resolve();
  });
}

describe("overlapping reads show the newest answer (SHE-325)", () => {
  it("the worklist keeps the newer read's list when an older read answers last", async () => {
    const { result } = renderHook(() => useLiveWorklist(() => undefined));
    await answer(0, reply(WORKLIST)); // the first load
    expect(result.current.state.status).toBe("ready");

    const stale = WORKLIST;
    const fresh = WORKLIST.filter((item) => item.session_id !== "ses_routine"); // e.g. closed since
    let older!: Promise<void>;
    let newer!: Promise<void>;
    act(() => {
      older = result.current.refresh(); // a live frame's refetch, started before the change
      newer = result.current.refresh(); // the reload after the change
    });
    await answer(2, reply(fresh));
    await act(async () => newer);
    await answer(1, reply(stale)); // the older read answers last
    await act(async () => older);
    const shown = result.current.state.status === "ready" ? result.current.state.data : [];
    expect(shown.map((item) => item.session_id)).toEqual(["ses_chest_pain"]);
  });
  it("shows an older read's answer at once when it comes first, then the newer one", async () => {
    const { result } = renderHook(() => useLiveWorklist(() => undefined));
    await answer(0, reply(WORKLIST));
    const first = WORKLIST.filter((item) => item.session_id === "ses_chest_pain");
    let older!: Promise<void>;
    let newer!: Promise<void>;
    act(() => {
      older = result.current.refresh();
      newer = result.current.refresh();
    });
    // The older read answers first: the nurse sees it now, without waiting for the newer one.
    await answer(1, reply(first));
    await act(async () => older);
    const ids = () => (result.current.state.status === "ready" ? result.current.state.data : []).map((i) => i.session_id);
    expect(ids()).toEqual(["ses_chest_pain"]);
    await answer(2, reply(WORKLIST));
    await act(async () => newer);
    expect(ids().sort()).toEqual(["ses_chest_pain", "ses_routine"]);
  });
});
