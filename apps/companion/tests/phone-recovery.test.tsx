vi.mock("@/components/PatientReceipt", () => ({ PatientReceipt: () => null }));
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ClaimForm } from "@/components/ClaimForm";
import { ClaimFromLink } from "@/components/ClaimFromLink";
import { MESSAGES } from "@/i18n/messages";
import { readSession, readToken, saveSession, saveToken } from "@/lib/session";

const { get, post, router } = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), router: { refresh: vi.fn(), push: vi.fn(), replace: vi.fn() },
}));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api", () => ({
  api: { POST: post, GET: get },
  createCareflowClientFor: () => ({ POST: post, GET: get }),
}));

const FIRST = { question_id: "q_complaint", text: "What brings you here?", key: "chief_complaint", notice: null };
const SECOND = { question_id: "q_duration", text: "When did this start?", key: "symptom_onset_at", notice: null };
const THIRD = { question_id: "q_severity", text: "How bad is the pain?", key: "pain_score", notice: null };
const CLAIM = { session_id: "ses_phone", session_token: "phone-token", language: "en", patient_display: "L. K.", first_question: FIRST };
const reply = (data: unknown, status = 200) => ({ data, response: { status } });

function savedVisit() {
  saveToken(CLAIM.session_token);
  saveSession({ sessionId: CLAIM.session_id, language: "en", display: CLAIM.patient_display });
}
function show(fromLink = true) {
  return render(<StrictMode><NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC">
    {fromLink ? <ClaimFromLink /> : <ClaimForm />}
  </NextIntlClientProvider></StrictMode>);
}

beforeEach(() => {
  vi.clearAllMocks();
  get.mockReset();
  get.mockResolvedValue(reply({ state: "intake", current_question: FIRST, intake_revision: 0 }));
  post.mockReset();
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/claim");
});

describe("phone visit recovery", () => {
  it("keeps the opening complaint free-form, with an empty editable answer and an example", async () => {
    savedVisit(); show();
    await screen.findByRole("heading", { name: FIRST.text });
    const answer = screen.getByRole("textbox", { name: "Or type your answer" });
    expect(answer).toHaveValue("");
    expect(answer).toHaveAttribute("placeholder", "Type your answer here");
    for (const key of ["chestPain", "breathing", "fever", "injury", "pain", "other"] as const) {
      expect(screen.queryByRole("button", { name: MESSAGES.en.intake.answers[key] })).not.toBeInTheDocument();
    }
    expect(post).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Example Ankle pain" }));
    expect(answer).toHaveValue("My ankle has been sore since yesterday.");
    expect(answer).toHaveFocus();
    expect(post).not.toHaveBeenCalled();
    await userEvent.clear(answer);
    await userEvent.type(answer, "My own description");
    expect(answer).toHaveValue("My own description");
  });

  it("claims a QR code once under Strict Mode, then reloads from the server without reusing it", async () => {
    window.history.replaceState(null, "", "/claim#t=one-time-qr-code-0001");
    post.mockResolvedValue(reply(CLAIM));
    const first = show();
    await screen.findByRole("heading", { name: FIRST.text });
    expect(post).toHaveBeenCalledTimes(1);
    expect(window.location.hash).toBe("");
    first.unmount();

    get.mockResolvedValue(reply({ state: "intake", current_question: SECOND, intake_revision: 1 }));
    show();
    await screen.findByRole("heading", { name: SECOND.text });
    expect(post).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledWith("/v1/sessions/{session_id}/status", { params: { path: { session_id: "ses_phone" } }, signal: expect.any(AbortSignal) });
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(FIRST.text);
  });

  it("reads the current server question after advancing and reloading the start page", async () => {
    savedVisit();
    get.mockResolvedValue(reply({ state: "intake", current_question: FIRST, intake_revision: 0 }));
    post.mockResolvedValue(reply({ turn_id: "turn_1", assistant_text: SECOND.text, done: false, next_question: SECOND, intake_revision: 1 }));
    const first = show(false);
    await screen.findByRole("heading", { name: FIRST.text });
    await userEvent.type(screen.getByRole("textbox", { name: "Or type your answer" }), "A sore ankle");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByRole("heading", { name: SECOND.text });
    first.unmount();

    get.mockResolvedValue(reply({ state: "intake", current_question: THIRD, intake_revision: 2 }));
    show(false);
    await screen.findByRole("heading", { name: THIRD.text });
    expect(post).toHaveBeenCalledTimes(1);
    expect(JSON.stringify({ ...sessionStorage })).not.toContain("A sore ankle");
  });

  it("clears an expired saved credential and offers reconnection", async () => {
    savedVisit();
    get.mockResolvedValue(reply(undefined, 401));
    show();
    expect(await screen.findByRole("alert")).toHaveTextContent("Your phone session has ended");
    expect(readToken()).toBeNull();
    expect(readSession()).toBeNull();
    expect(post).not.toHaveBeenCalled();
  });

  it.each([undefined, null])("does not treat a missing intake question (%s) as completion", async (question) => {
    savedVisit();
    get.mockResolvedValueOnce(reply({ state: "intake", current_question: question, intake_revision: 0 }))
      .mockResolvedValueOnce(reply({ state: "intake", current_question: SECOND, intake_revision: 1 }));
    show();
    await screen.findByRole("alert");
    expect(screen.queryByText(/A nurse will see you shortly/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByRole("heading", { name: SECOND.text });
  });

  it("restores a queued visit with a route to current queue status", async () => {
    savedVisit();
    get.mockResolvedValue(reply({ state: "queued", token_code: "G-031", intake_revision: 3 }));
    show();
    expect(await screen.findByRole("link", { name: "See my place in the queue" })).toHaveAttribute("href", "/status");
    expect(post).not.toHaveBeenCalled();
  });

  it.each(["closed", "no_show"])("ends a %s visit instead of presenting a nurse waiting screen", async (state) => {
    savedVisit();
    get.mockResolvedValue(reply({ state, intake_revision: 3 }));
    show();
    expect(await screen.findByRole("alert")).toHaveTextContent("Your phone session has ended");
    expect(readToken()).toBeNull();
    expect(screen.queryByText(/A nurse will see you shortly/)).not.toBeInTheDocument();
  });

  it.each(["called", "arrived"])("opens current status for a %s visit without telling the patient to keep waiting", async (state) => {
    savedVisit();
    get.mockResolvedValue(reply({ state, token_code: "G-031", room: "Room 4", intake_revision: 3 }));
    show();
    await screen.findByText("Opening your current visit status…");
    // The redirect runs in an effect after that render: wait for it (a loaded machine reached the
    // assertion first, docs/FLAKY.md).
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/status"));
    expect(screen.queryByText(/stay in the waiting area/)).not.toBeInTheDocument();
  });

  it("retries a failed QR network request with the same in-memory code and claim key", async () => {
    window.history.replaceState(null, "", "/claim#t=one-time-qr-code-0002");
    post.mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValueOnce(reply(CLAIM));
    show();
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not reach the clinic");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByRole("heading", { name: FIRST.text });
    expect(post).toHaveBeenCalledTimes(2);
    const [first, retry] = post.mock.calls.map((call) => (call[1] as { body: { qr_token: string; claim_key: string } }).body);
    // The server answers the claiming phone's retry with a new credential (a lost answer), so
    // the retry must prove it is the same phone: the same code and the same random key.
    expect(retry).toEqual(first);
    expect(retry?.qr_token).toBe("one-time-qr-code-0002");
    expect(retry?.claim_key).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(window.sessionStorage.getItem("careflow.companion.session-token")).toBe(CLAIM.session_token);
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain(retry?.claim_key ?? "?");
  });

  it("makes a new claim key for every code", async () => {
    window.history.replaceState(null, "", "/claim#t=one-time-qr-code-0005");
    post.mockResolvedValue(reply(CLAIM));
    const first = show();
    await screen.findByRole("heading", { name: FIRST.text });
    first.unmount();
    window.sessionStorage.clear();
    window.history.replaceState(null, "", "/claim#t=one-time-qr-code-0006");
    show();
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    const keys = post.mock.calls.map((call) => (call[1] as { body: { claim_key: string } }).body.claim_key);
    expect(new Set(keys).size).toBe(2);
  });

  it("retries the saved visit, not the consumed QR, if its first status read fails", async () => {
    window.history.replaceState(null, "", "/claim#t=one-time-qr-code-0003");
    post.mockResolvedValue(reply({ ...CLAIM, first_question: null }));
    get.mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(reply({ state: "awaiting_nurse", intake_revision: 3 }));
    show();
    await screen.findByRole("alert");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByRole("link", { name: "See my place in the queue" });
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("recovers manual code entry after a network failure", async () => {
    post.mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValueOnce(reply(CLAIM));
    show(false);
    await waitFor(() => expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled());
    await userEvent.type(screen.getByLabelText("Hand-over code"), "manual-qr-code-0001");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("alert");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByRole("heading", { name: FIRST.text });
    expect(post).toHaveBeenCalledTimes(2);
  });

  it("uses the advanced server checkpoint after a lost turn response without resending", async () => {
    savedVisit();
    get.mockResolvedValueOnce(reply({ state: "intake", current_question: FIRST, intake_revision: 0 }))
      .mockResolvedValueOnce(reply({ state: "intake", current_question: SECOND, intake_revision: 1 }));
    post.mockRejectedValueOnce(new TypeError("Response lost"));
    show();
    await screen.findByRole("heading", { name: FIRST.text });
    await userEvent.type(screen.getByRole("textbox"), "A sore ankle");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByRole("heading", { name: SECOND.text });
    expect(post).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("textbox")).toHaveValue("");
  });

  it("blocks another turn until an uncertain failure is reconciled, then permits an explicit retry", async () => {
    savedVisit();
    get.mockResolvedValueOnce(reply({ state: "intake", current_question: FIRST, intake_revision: 0 }))
      .mockRejectedValueOnce(new TypeError("Offline"))
      .mockResolvedValueOnce(reply({ state: "intake", current_question: FIRST, intake_revision: 0 }));
    post.mockRejectedValueOnce(new TypeError("Offline"))
      .mockResolvedValueOnce(reply({ turn_id: "turn_retry", assistant_text: SECOND.text, done: false, next_question: SECOND, intake_revision: 1 }));
    show();
    await screen.findByRole("heading", { name: FIRST.text });
    await userEvent.type(screen.getByRole("textbox"), "A sore ankle");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByRole("button", { name: "Check current question" });
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Check current question" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Your answer did not go through");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByRole("heading", { name: SECOND.text });
    expect(post).toHaveBeenCalledTimes(2);
  });

  it("handles token expiry during a turn without leaving the form stuck", async () => {
    savedVisit();
    get.mockResolvedValue(reply({ state: "intake", current_question: FIRST, intake_revision: 0 }));
    post.mockResolvedValue(reply(undefined, 401));
    show();
    await screen.findByRole("heading", { name: FIRST.text });
    await userEvent.type(screen.getByRole("textbox"), "A sore ankle");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Your phone session has ended");
    expect(readToken()).toBeNull();
  });

  it("reconnects the manual root-page flow after token expiry without requiring navigation to another page", async () => {
    post.mockImplementation(async (path: string) => path === "/v1/checkin/qr/claim" ? reply(CLAIM) : reply(undefined, 401));
    show(false);
    await waitFor(() => expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled());
    await userEvent.type(screen.getByLabelText("Hand-over code"), "manual-qr-code-expiry");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { name: FIRST.text });
    await userEvent.type(screen.getByRole("textbox"), "A sore ankle");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText(/Your phone session has ended/);
    await userEvent.click(screen.getByRole("button", { name: "Enter a code" }));
    await userEvent.type(screen.getByLabelText("Hand-over code"), "manual-qr-code-renewed");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { name: FIRST.text });
    expect(post.mock.calls.filter((call) => call[0] === "/v1/checkin/qr/claim")).toHaveLength(2);
    expect(router.push).not.toHaveBeenCalled();
  });

  it.each([-1, 1.5])("reconciles an invalid POST revision %s instead of enabling another answer with it", async (revision) => {
    savedVisit();
    get.mockResolvedValueOnce(reply({ state: "intake", current_question: FIRST, intake_revision: 1 }))
      .mockResolvedValueOnce(reply({ state: "intake", current_question: SECOND, intake_revision: 5 }));
    post.mockResolvedValueOnce(reply({ turn_id: "turn_fixture", assistant_text: "Next question", done: false, next_question: THIRD, intake_revision: revision }))
      .mockResolvedValueOnce(reply({ turn_id: "turn_fixture", assistant_text: "Next question", done: false, next_question: THIRD, intake_revision: 9 }));
    show();
    await screen.findByRole("heading", { name: FIRST.text });
    await userEvent.type(screen.getByRole("textbox"), "A sore ankle");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByRole("heading", { name: SECOND.text });
    await userEvent.type(screen.getByRole("textbox"), "Today");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByRole("heading", { name: THIRD.text });
    expect(post.mock.calls.map((call) => call[1].body.expected_intake_revision)).toEqual([1, 5]);
  });

  it("offers recovery after an HTTP 503 claim and reports a rejected code distinctly", async () => {
    window.history.replaceState(null, "", "/claim#t=one-time-qr-code-0004");
    post.mockResolvedValueOnce(reply(undefined, 503)).mockResolvedValueOnce(reply(undefined, 410));
    show();
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not reach the clinic");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This code did not work");
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("reconciles an HTTP turn error before allowing the same answer to be submitted again", async () => {
    savedVisit();
    get.mockResolvedValue(reply({ state: "intake", current_question: FIRST, intake_revision: 0 }));
    post.mockResolvedValue(reply(undefined, 422));
    show();
    await screen.findByRole("heading", { name: FIRST.text });
    await userEvent.type(screen.getByRole("textbox"), "An unclear answer");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Your answer did not go through");
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
    expect(get).toHaveBeenCalledTimes(2);
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("refuses to send answers without an authoritative server revision", async () => {
    savedVisit();
    get.mockResolvedValue(reply({ state: "intake", current_question: FIRST }));
    show();
    await screen.findByRole("alert");
    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("uses the server revision when a side question keeps the same question ID", async () => {
    savedVisit();
    get.mockResolvedValueOnce(reply({ state: "intake", current_question: FIRST, intake_revision: 7 }))
      .mockResolvedValueOnce(reply({ state: "intake", current_question: { ...FIRST, notice: "Ask at reception." }, intake_revision: 11 }));
    post.mockRejectedValueOnce(new TypeError("Response lost"))
      .mockResolvedValueOnce(reply({ turn_id: "turn_fixture", assistant_text: "Next question", done: false, next_question: SECOND, intake_revision: 16 }));
    show();
    await screen.findByRole("heading", { name: FIRST.text });
    await userEvent.type(screen.getByRole("textbox"), "Where is reception?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Ask at reception.");
    expect(screen.getByRole("textbox")).toHaveValue("");
    await userEvent.type(screen.getByRole("textbox"), "A sore ankle");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByRole("heading", { name: SECOND.text });
    expect(post.mock.calls.map((call) => call[1].body.expected_intake_revision)).toEqual([7, 11]);
  });

  it("handles a stale-revision 409 by loading the accepted question without resending", async () => {
    savedVisit();
    get.mockResolvedValueOnce(reply({ state: "intake", current_question: FIRST, intake_revision: 1 }))
      .mockResolvedValueOnce(reply({ state: "intake", current_question: SECOND, intake_revision: 3 }));
    post.mockResolvedValue(reply(undefined, 409));
    show();
    await screen.findByRole("heading", { name: FIRST.text });
    await userEvent.type(screen.getByRole("textbox"), "A sore ankle");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByRole("heading", { name: SECOND.text });
    expect(post.mock.calls[0]?.[1].body.expected_intake_revision).toBe(1);
    expect(post).toHaveBeenCalledTimes(1);
  });
});
