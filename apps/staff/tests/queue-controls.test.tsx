import type { Schema } from "@careflow/contracts";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { QueueControls } from "@/components/nurse/QueueControls";
import { MESSAGES } from "@/i18n/messages";
import type { WorklistItem } from "@/lib/sortWorklist";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: { POST: post } }));

type QueueToken = Schema<"QueueToken">;
const token = (id: string, status: QueueToken["status"], extra: Partial<QueueToken> = {}): QueueToken => ({
  token_id: `tok_${id}`,
  session_id: `ses_${id}`,
  token_code: `A-00${id}`,
  department: "emergency",
  status,
  issued_at: "2026-09-26T09:00:00Z",
  ...extra,
});
const row = (queueToken: QueueToken): WorklistItem => ({
  session_id: queueToken.session_id,
  patient_display: "M. S.",
  arrived_at: "2026-09-26T08:55:00Z",
  state: "queued",
  alerts: [],
  open_tasks: [],
  token_code: queueToken.token_code,
  queue_token: queueToken,
});
const reply = (data: unknown, status = 200) => ({ data, response: { status } });

function show(rows: WorklistItem[], onChanged = vi.fn()) {
  render(
    <NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC">
      <QueueControls rows={rows} onChanged={onChanged} />
    </NextIntlClientProvider>,
  );
  return onChanged;
}

beforeEach(() => post.mockReset());

describe("the nurse's queue controls (M2-07)", () => {
  it("call the next patient of a department and say where", async () => {
    post.mockResolvedValue(reply({ token: token("1", "called", { room: "Resus 1" }) }));
    const onChanged = show([row(token("1", "waiting")), row(token("2", "waiting", { department: "general" }))]);
    expect(screen.getAllByRole("button", { name: /^Call next/ }).map((button) => button.textContent)).toEqual([
      "Call next: Emergency",
      "Call next: General",
    ]);
    await userEvent.click(screen.getByRole("button", { name: "Call next: Emergency" }));
    expect(post).toHaveBeenCalledWith("/v1/queue/{department}/call-next", { params: { path: { department: "emergency" } } });
    expect(await screen.findByRole("status")).toHaveTextContent("Called A-001 to Resus 1.");
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("say so when no room is free", async () => {
    post.mockResolvedValue(reply(undefined, 409));
    const onChanged = show([row(token("1", "waiting"))]);
    await userEvent.click(screen.getByRole("button", { name: "Call next: Emergency" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No free room");
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("mark a called patient arrived, a no-show or seen, which frees the room", async () => {
    post.mockImplementation(async () => reply(token("1", "closed")));
    show([row(token("1", "called", { room: "ED 1" })), row(token("2", "arrived", { room: "ED 2" }))]);
    const [calledRow, arrivedRow] = screen.getAllByTestId("called-token");
    expect(calledRow).toHaveTextContent("A-001");
    expect(calledRow).toHaveTextContent("Room: ED 1");
    expect(arrivedRow).not.toHaveTextContent("No-show"); // an arrived patient can only be seen

    await userEvent.click(screen.getAllByRole("button", { name: "Arrived" })[0]!);
    await waitFor(() => expect(post).toHaveBeenLastCalledWith("/v1/queue/tokens/{token_id}/arrived", { params: { path: { token_id: "tok_1" } } }));
    await userEvent.click(screen.getAllByRole("button", { name: "No-show" })[0]!);
    await waitFor(() => expect(post).toHaveBeenLastCalledWith("/v1/queue/tokens/{token_id}/no-show", { params: { path: { token_id: "tok_1" } } }));
    await userEvent.click(screen.getAllByRole("button", { name: "Seen (free the room)" })[1]!);
    await waitFor(() => expect(post).toHaveBeenLastCalledWith("/v1/queue/tokens/{token_id}/close", { params: { path: { token_id: "tok_2" } } }));
    expect(await screen.findByRole("status")).toHaveTextContent("A-002 seen; the room is free.");
  });

  it("stay out of the way with nobody waiting or called", () => {
    show([row(token("1", "closed")), row(token("2", "no_show"))]);
    expect(screen.queryByTestId("queue-controls")).not.toBeInTheDocument();
  });
});
