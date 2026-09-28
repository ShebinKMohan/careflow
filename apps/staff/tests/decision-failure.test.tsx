import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { Schema } from "@careflow/contracts";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import { News2Form } from "@/components/nurse/News2Form";
import { TriageCard } from "@/components/nurse/TriageCard";
import { MESSAGES } from "@/i18n/messages";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: { POST: post } }));
const TRIAGE = JSON.parse(readFileSync(resolve(process.cwd(), "tests/fixtures/api/triage.json"), "utf8")) as Schema<"TriageView">;

describe("staff actions recover from transport failures", () => {
  it("offers confirmation when a fresh suggestion arrives before the nurse has edited", async () => {
    const card = (triage: Schema<"TriageView">) => <NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC"><TriageCard triage={triage} facts={new Map()} warnings={[]} nurseUserId="nurse_1" onDecided={vi.fn()} /></NextIntlClientProvider>;
    const { rerender } = render(card({ ...TRIAGE, suggestion: null }));
    expect(screen.getByRole("textbox")).toHaveValue("no model suggestion");
    rerender(card(TRIAGE));
    expect(screen.getByRole("button", { name: "Confirm level 2" })).toBeVisible();
    expect(screen.queryByRole("region", { name: "Change level" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Change level" }));
    expect(screen.getByRole("textbox")).toHaveValue("");
  });

  it("preserves the nurse's chosen level and exact edited reason when a suggestion arrives", async () => {
    const card = (triage: Schema<"TriageView">) => <NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC"><TriageCard triage={triage} facts={new Map()} warnings={[]} nurseUserId="nurse_1" onDecided={vi.fn()} /></NextIntlClientProvider>;
    const { rerender } = render(card({ ...TRIAGE, suggestion: null }));
    await userEvent.click(screen.getByRole("button", { name: "KTAS 3" }));
    const reason = screen.getByRole("textbox");
    await userEvent.clear(reason);
    await userEvent.type(reason, "Assessed in person; additional findings");
    rerender(card(TRIAGE));
    expect(screen.getByRole("button", { name: "KTAS 3" })).toHaveAttribute("aria-pressed", "true");
    expect(reason).toHaveValue("Assessed in person; additional findings");
    expect(screen.getByRole("button", { name: "Set level 3" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Confirm level 2" })).not.toBeInTheDocument();
  });

  it("does not report a decision or leave confirmation disabled when the network rejects", async () => {
    post.mockRejectedValue(new TypeError("Failed to fetch"));
    const onDecided = vi.fn();
    render(<NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC"><TriageCard triage={TRIAGE} facts={new Map()} warnings={[]} nurseUserId="nurse_1" onDecided={onDecided} /></NextIntlClientProvider>);
    const confirm = screen.getByRole("button", { name: "Confirm level 2" });
    await userEvent.click(confirm);
    expect(await screen.findByRole("alert")).toHaveTextContent("The decision was not saved");
    expect(confirm).toBeEnabled();
    expect(onDecided).not.toHaveBeenCalled();
    expect(screen.queryByTestId("decision-result")).not.toBeInTheDocument();
  });

  it("preserves entered vitals and enables saving again after a network rejection", async () => {
    post.mockRejectedValue(new TypeError("Failed to fetch"));
    const onSaved = vi.fn();
    render(<NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC"><News2Form sessionId={TRIAGE.session_id} news2={null} onSaved={onSaved} /></NextIntlClientProvider>);
    const respiratoryRate = screen.getByLabelText("Respiratory rate (/min)");
    await userEvent.type(respiratoryRate, "18");
    await userEvent.click(screen.getByRole("button", { name: "Save vitals" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The vitals were not saved");
    expect(respiratoryRate).toHaveValue("18");
    expect(screen.getByRole("button", { name: "Save vitals" })).toBeEnabled();
    expect(onSaved).not.toHaveBeenCalled();
  });
});
