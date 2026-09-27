import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TokenRenewer } from "@/components/TokenRenewer";
import { RENEW_BEFORE_MS, renewToken, RETRY_MS, tokenExpiry } from "@/lib/renewal";
import { readSession, readToken, saveSession, saveToken } from "@/lib/session";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@careflow/contracts/client", () => ({ createCareflowClient: () => ({ POST: post }) }));

const NOW = Date.UTC(2026, 8, 27, 9, 0, 0);
/** An unsigned JWT-shaped token: the phone only reads `exp` (the API verifies every token). */
function tokenExpiringIn(ms: number, tag = "a"): string {
  const payload = btoa(JSON.stringify({ exp: Math.floor((NOW + ms) / 1000), tag }))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `header.${payload}.signature`;
}
const reply = (data: unknown, status = 200) => ({ data, response: { status } });
function visit(token: string) {
  saveSession({ sessionId: "ses_phone", language: "en", display: "L. K." });
  saveToken(token);
}

beforeEach(() => {
  vi.useFakeTimers({ now: NOW, toFake: ["setTimeout", "clearTimeout", "Date"] });
  post.mockReset();
  window.sessionStorage.clear();
});
afterEach(() => vi.useRealTimers());

describe("the phone's token renewal", () => {
  it("reads when a token runs out, and nothing from a malformed one", () => {
    expect(tokenExpiry(tokenExpiringIn(30 * 60_000))).toBe(NOW + 30 * 60_000);
    expect(tokenExpiry("not-a-jwt")).toBeNull();
    expect(tokenExpiry("a.%%%.c")).toBeNull();
  });

  it("stores the new token, and clears the visit when the API says it is over", async () => {
    visit(tokenExpiringIn(5 * 60_000));
    const renewed = tokenExpiringIn(30 * 60_000, "b");
    post.mockResolvedValueOnce(reply({ session_token: renewed, expires_at: "2026-09-27T09:30:00Z" }));
    await expect(renewToken()).resolves.toBe("renewed");
    expect(post).toHaveBeenCalledWith("/v1/sessions/{session_id}/renew", { params: { path: { session_id: "ses_phone" } } });
    expect(readToken()).toBe(renewed);

    for (const status of [401, 403, 404, 409]) {
      visit(tokenExpiringIn(5 * 60_000));
      post.mockResolvedValueOnce(reply(undefined, status));
      await expect(renewToken()).resolves.toBe("ended");
      expect([readToken(), readSession()]).toEqual([null, null]);
    }
  });

  it("keeps the token when the API cannot be reached, to try again", async () => {
    const token = tokenExpiringIn(5 * 60_000);
    visit(token);
    post.mockResolvedValueOnce(reply(undefined, 503));
    await expect(renewToken()).resolves.toBe("unavailable");
    post.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(renewToken()).resolves.toBe("unavailable");
    expect(readToken()).toBe(token);
  });

  it("renews 10 minutes before the token runs out, then again for the new token", async () => {
    visit(tokenExpiringIn(12 * 60_000));
    const renewed = tokenExpiringIn(12 * 60_000 + 30 * 60_000, "b");
    post.mockResolvedValue(reply({ session_token: renewed, expires_at: "2026-09-27T09:42:00Z" }));
    render(<TokenRenewer />);
    await act(async () => void vi.advanceTimersByTime(12 * 60_000 - RENEW_BEFORE_MS - 1));
    expect(post).not.toHaveBeenCalled();
    await act(async () => void vi.advanceTimersByTime(1));
    expect(post).toHaveBeenCalledTimes(1);
    expect(readToken()).toBe(renewed);
    await act(async () => void vi.advanceTimersByTime(30 * 60_000 - 1));
    expect(post).toHaveBeenCalledTimes(1);
    await act(async () => void vi.advanceTimersByTime(1));
    expect(post).toHaveBeenCalledTimes(2);
  });

  it("retries soon after a failed renewal, and at once when a sleeping phone wakes near expiry", async () => {
    visit(tokenExpiringIn(20 * 60_000));
    post.mockResolvedValueOnce(reply(undefined, 503));
    render(<TokenRenewer />);
    await act(async () => void vi.advanceTimersByTime(10 * 60_000));
    expect(post).toHaveBeenCalledTimes(1);
    post.mockResolvedValueOnce(reply(undefined, 503));
    await act(async () => void vi.advanceTimersByTime(RETRY_MS));
    expect(post).toHaveBeenCalledTimes(2);

    // Asleep: the tab's timers stop; on waking with 5 minutes left it renews at once.
    vi.setSystemTime(NOW + 15 * 60_000);
    post.mockResolvedValueOnce(reply({ session_token: tokenExpiringIn(45 * 60_000, "c"), expires_at: "2026-09-27T09:45:00Z" }));
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(post).toHaveBeenCalledTimes(3);
  });

  it("does nothing without a visit on this phone", async () => {
    render(<TokenRenewer />);
    await act(async () => void vi.advanceTimersByTime(60 * 60_000));
    expect(post).not.toHaveBeenCalled();
  });
});
