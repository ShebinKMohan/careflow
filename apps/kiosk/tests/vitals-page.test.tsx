import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { useEffect } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { VitalsPage } from "@/components/VitalsPage";
import { MESSAGES } from "@/i18n/messages";

const push = vi.fn();
const stopped = vi.fn();
const session = { sessionId: "same-saved-visit", token: "patient-proof" };
const draft = { urgentRequest: false, name: "Alex Morgan", age: "34", sex: "male", phone: "", departmentId: "cardiology", doctorId: "", purpose: "follow_up", reason: "Routine follow-up", emergencySymptoms: [] };
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), useSearchParams: () => new URLSearchParams() }));
vi.mock("@/lib/session", () => ({ useKiosk: () => ({ patient: session, receptionDraft: draft }) }));
vi.mock("@/components/VitalsPanel", () => ({ VitalsPanel: ({ sessionId }: { sessionId: string }) => { useEffect(() => stopped, []); return <div data-testid="same-visit-panel">{sessionId}</div>; } }));

beforeEach(() => { vi.clearAllMocks(); draft.urgentRequest = false; draft.name = "Alex Morgan"; });

describe("saved visit summary back navigation", () => {
  it("unmounts measurement, shows a read-only summary, and returns to the same visit", () => {
    render(<NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="Asia/Dubai"><VitalsPage /></NextIntlClientProvider>);
    expect(screen.getByTestId("same-visit-panel")).toHaveTextContent("same-saved-visit");
    fireEvent.click(screen.getByRole("button", { name: "Back to visit summary" }));
    expect(stopped).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("same-visit-panel")).not.toBeInTheDocument();
    expect(screen.getByText("Alex Morgan")).toBeInTheDocument();
    expect(screen.getByText(/Your registration is already saved/)).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /submit|edit/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Return to health check" }));
    expect(screen.getByTestId("same-visit-panel")).toHaveTextContent("same-saved-visit");
    expect(push).not.toHaveBeenCalled();
  });

  it("stops measurement and returns an anonymous urgent visit to its saved request without replacing the session", () => {
    draft.urgentRequest = true;
    draft.name = "";
    render(<NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="Asia/Dubai"><VitalsPage /></NextIntlClientProvider>);
    expect(screen.getByTestId("same-visit-panel")).toHaveTextContent("same-saved-visit");
    expect(screen.queryByRole("button", { name: "Back to visit summary" })).not.toBeInTheDocument();
    expect(screen.getByText(MESSAGES.en.reception.helpRecorded)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: MESSAGES.en.reception.back }));
    expect(push).toHaveBeenCalledExactlyOnceWith("/checkin");
    expect(stopped).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("same-visit-panel")).not.toBeInTheDocument();
    expect(session).toEqual({ sessionId: "same-saved-visit", token: "patient-proof" });
    expect(draft.urgentRequest).toBe(true);
    expect(screen.getByRole("button", { name: MESSAGES.en.reception.back })).toBeDisabled();
  });
});
