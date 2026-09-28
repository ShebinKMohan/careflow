import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FeelsWorse } from "@/components/FeelsWorse";
import { MESSAGES } from "@/i18n/messages";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: { POST: post } }));

function show() {
  return render(<NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC"><FeelsWorse childSession={false} /></NextIntlClientProvider>);
}

beforeEach(() => { post.mockReset(); });

describe("the nurse alert sheet", () => {
  it("sends an unanswered drowsiness question as unknown, never as a patient denial", async () => {
    post.mockResolvedValue({ data: { alert_id: "alert_synthetic" } });
    show();
    await userEvent.click(screen.getByRole("button", { name: "I feel worse" }));
    await userEvent.click(screen.getByRole("button", { name: "Tell the nurse" }));
    expect(post).toHaveBeenCalledWith("/v1/companion/feels-worse", {
      signal: expect.any(AbortSignal),
      body: { answers: { chest_or_breathing: [], face_arm_speech: [], drowsy_or_confused: "not_sure" } },
    });
    expect(await screen.findByRole("status")).toHaveTextContent("The nurse has been told");
  });

  it("keeps keyboard focus in the sheet and returns it to the alert button on Escape", async () => {
    show();
    const trigger = screen.getByRole("button", { name: "I feel worse" });
    await userEvent.click(trigger);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await userEvent.tab();
    expect(within(dialog).getByRole("button", { name: "Chest pain" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });


  it("releases a stalled alert after ten seconds and directs the patient to reception", async () => {
    vi.useFakeTimers();
    try {
      post.mockImplementation((_path: string, options: { signal: AbortSignal }) => new Promise((_resolve, reject) => {
        options.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      }));
      show();
      fireEvent.click(screen.getByRole("button", { name: "I feel worse" }));
      fireEvent.click(screen.getByRole("button", { name: "Tell the nurse" }));
      expect(screen.getByRole("button", { name: "Telling the nurse…" })).toBeDisabled();
      await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
      expect(screen.getByRole("alert")).toHaveTextContent("Tell the reception desk now");
      expect(screen.getByRole("button", { name: "Tell the nurse" })).toBeEnabled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not claim success when the clinic cannot be reached", async () => {
    post.mockRejectedValue(new TypeError("Offline"));
    show();
    await userEvent.click(screen.getByRole("button", { name: "I feel worse" }));
    await userEvent.click(screen.getByRole("button", { name: "Tell the nurse" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Tell the reception desk now");
    expect(screen.queryByTestId("feels-worse-told")).not.toBeInTheDocument();
  });
});
