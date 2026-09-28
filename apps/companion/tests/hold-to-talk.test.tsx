import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HoldToTalk } from "@/components/HoldToTalk";
import { saveSession } from "@/lib/session";
import { MESSAGES } from "@/i18n/messages";
import type { VoiceClient, VoiceEvents } from "@/lib/voice";

const session = { sessionId: "ses_test", token: "test-token", language: "en" as const };
function setup(pending = false) {
  let grant = () => {};
  let callbacks: VoiceEvents | undefined;
  const client = {
    available: true,
    start: vi.fn((_session, events) => {
      callbacks = events;
      return pending ? new Promise<void>((resolve) => { grant = resolve; }) : Promise.resolve();
    }),
    finishUtterance: vi.fn(() => !pending), stop: vi.fn(), abort: vi.fn(),
  } satisfies VoiceClient;
  const result = render(<NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC">
    <HoldToTalk session={session} client={client} onQuestion={vi.fn()} onTranscript={vi.fn()} />
    <input aria-label="Name" /><textarea aria-label="Answer" /><div contentEditable aria-label="Editable" /><div role="dialog" aria-label="Nurse help" tabIndex={-1} />
  </NextIntlClientProvider>);
  return { ...result, client, grant: () => grant(), callbacks: () => callbacks };
}
beforeEach(() => { saveSession({ ...session, display: "D. E.", voiceConsent: true }); });
afterEach(() => { sessionStorage.clear(); vi.useRealTimers(); });

it("holds Space once, ignores auto-repeat, and releases to process with microphone off", async () => {
  const t = setup();
  expect(t.client.start).not.toHaveBeenCalled();
  fireEvent.keyDown(window, { code: "Space" });
  fireEvent.keyDown(window, { code: "Space", repeat: true });
  await screen.findByRole("button", { name: /Listening/ });
  expect(t.client.start).toHaveBeenCalledTimes(1);
  expect(t.client.start.mock.calls[0]?.[0]).toMatchObject({ inputMode: "push_to_talk" });
  fireEvent.keyUp(window, { code: "Space" });
  expect(t.client.finishUtterance).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: /Processing/ })).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent("Microphone off");
  act(() => t.callbacks()?.onQuestion({ question_id: "q2", text: "When?", key: null, notice: null }));
  expect(screen.getByRole("button", { name: /Hold to speak/ })).toBeEnabled();
  expect(t.client.start).toHaveBeenCalledTimes(1); // playback/question does not reopen mic
});

it("does not steal spaces from text fields or editable elements", () => {
  const t = setup();
  for (const label of ["Name", "Answer", "Editable", "Nurse help"]) fireEvent.keyDown(screen.getByLabelText(label), { code: "Space" });
  expect(t.client.start).not.toHaveBeenCalled();
});

it("release during permission setup remains off when permission later resolves", async () => {
  const t = setup(true);
  fireEvent.keyDown(window, { code: "Space" });
  fireEvent.keyUp(window, { code: "Space" });
  await act(async () => t.grant());
  expect(screen.getByRole("button", { name: /Hold to speak/ })).toBeEnabled();
  expect(screen.getByRole("alert")).toHaveTextContent("Hold again");
});

it("cancels capture on blur and ignores late transcripts", async () => {
  const t = setup();
  fireEvent.keyDown(window, { code: "Space" });
  await screen.findByRole("button", { name: /Listening/ });
  fireEvent.blur(window);
  expect(t.client.abort).toHaveBeenCalled();
  expect(t.client.finishUtterance).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByRole("button", { name: /Hold to speak/ })).toBeEnabled());
});

it("never displays or activates without saved voice consent", () => {
  saveSession({ ...session, display: "D. E.", voiceConsent: false });
  const t = setup();
  fireEvent.keyDown(window, { code: "Space" });
  expect(t.client.start).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: /Hold to speak/ })).toBeNull();
});

it("pressing and releasing the microphone button submits one held answer", async () => {
  const t = setup();
  const button = screen.getByRole("button", { name: /Hold to speak/ });
  fireEvent(button, new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
  await screen.findByRole("button", { name: /Listening/ });
  fireEvent.pointerUp(button);
  fireEvent.pointerUp(window);
  expect(t.client.start).toHaveBeenCalledTimes(1);
  expect(t.client.finishUtterance).toHaveBeenCalledTimes(1);
});

it("pointer cancellation discards the held audio instead of sending it", async () => {
  const t = setup();
  const button = screen.getByRole("button", { name: /Hold to speak/ });
  fireEvent(button, new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
  await screen.findByRole("button", { name: /Listening/ });
  fireEvent.pointerCancel(button);
  expect(t.client.abort).toHaveBeenCalled();
  expect(t.client.finishUtterance).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: /Hold to speak/ })).toBeEnabled();
});

it("automatically releases before the server's 30-second capture cap", async () => {
  vi.useFakeTimers();
  const t = setup();
  await act(async () => { fireEvent.keyDown(window, { code: "Space" }); });
  act(() => vi.advanceTimersByTime(29_000));
  expect(t.client.finishUtterance).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: /Processing/ })).toBeDisabled();
});

it("keeps privacy and keyboard instructions behind a compact disclosure", () => {
  setup();
  const help = screen.getByLabelText("Voice instructions and privacy");
  expect(help.closest("details")).not.toHaveAttribute("open");
  expect(screen.getByRole("status")).toHaveTextContent("Microphone off");
  fireEvent.click(help);
  expect(help.closest("details")).toHaveAttribute("open");
  expect(screen.getByText(/Audio is processed locally and is not saved/)).toBeInTheDocument();
});
