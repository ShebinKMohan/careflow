import type { Schema } from "@careflow/contracts";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionReception } from "@/components/ReceptionSummary";
import { Worklist } from "@/components/Worklist";
import { QueueControls } from "@/components/nurse/QueueControls";
import { MESSAGES } from "@/i18n/messages";
import type { WorklistItem } from "@/lib/sortWorklist";

const { get, post, liveWorklist } = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), liveWorklist: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ api: { GET: get, POST: post } }));
vi.mock("@/lib/useLiveWorklist", () => ({ useLiveWorklist: liveWorklist }));

const RECEPTION: Schema<"ReceptionContext"> = {
  patient: { name: "Amina Rahman", age_years: 42, sex: "female", phone: "+971500000123" },
  department_id: "cardiology",
  department_name: "Cardiology",
  doctor_id: "doctor-demo",
  doctor_name: "Dr. Layla Hassan",
  visit_purpose: "follow_up",
  reason: "Review my recent heart test results.",
  emergency_symptoms: ["chest_pain"],
  registered_at: "2026-09-28T09:00:00Z",
  availability_notice: "Example directory only; availability requires staff confirmation.",
};
const row = (id: string, extra: Partial<WorklistItem> = {}): WorklistItem => ({
  session_id: id,
  patient_display: "A. R.",
  arrived_at: "2026-09-28T09:00:00Z",
  state: "awaiting_nurse",
  alerts: [],
  open_tasks: [],
  ...extra,
});
const reply = (data: unknown, status = 200) => ({ data, response: { status } });
const show = (children: ReactNode) => render(
  <NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC">
    {children}
  </NextIntlClientProvider>,
);

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  liveWorklist.mockReset();
});

describe("staff reception context", () => {
  it("loads the exact session detail and labels registration and visit facts as patient-stated", async () => {
    get.mockResolvedValue(reply(RECEPTION));
    show(<SessionReception sessionId="ses_reception_42" />);

    const summary = await screen.findByTestId("reception-summary");
    expect(get).toHaveBeenCalledWith("/v1/sessions/{session_id}/reception", {
      params: { path: { session_id: "ses_reception_42" } },
    });
    for (const text of ["Amina Rahman", "+971500000123", "Follow-up visit", RECEPTION.reason]) {
      const fact = within(summary).getByText(text).closest("dd");
      expect(fact).not.toBeNull();
      expect(fact?.querySelector('[data-provenance="PATIENT_STATED"]')).toBeInTheDocument();
    }
    const preference = within(summary).getByTestId("reception-preference");
    expect(preference).toHaveTextContent("Patient preference");
    expect(preference).toHaveTextContent("Cardiology");
    expect(preference).toHaveTextContent("Dr. Layla Hassan");
    expect(preference).toHaveTextContent("Not booked");
    expect(summary).toHaveTextContent(RECEPTION.availability_notice);
    const symptoms = within(summary).getByRole("region", { name: "Emergency symptoms reported at reception" });
    expect(symptoms).toHaveTextContent("Chest pain");
    expect(symptoms.querySelector('[data-provenance="PATIENT_STATED"]')).toBeInTheDocument();
  });

  it("quietly omits reception details for a legacy session returning 404", async () => {
    get.mockResolvedValue(reply(undefined, 404));
    const { container } = show(<SessionReception sessionId="ses_legacy" />);

    await waitFor(() => expect(container).toBeEmptyDOMElement());
    expect(get).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Retry reception details" })).not.toBeInTheDocument();
  });

  it.each(["http", "network"])("shows a retry after a %s failure and recovers the saved details", async (failure) => {
    if (failure === "network") get.mockRejectedValueOnce(new Error("offline"));
    else get.mockResolvedValueOnce(reply(undefined, 503));
    get.mockResolvedValueOnce(reply(RECEPTION));
    show(<SessionReception sessionId="ses_retry" />);

    const retry = await screen.findByRole("button", { name: "Retry reception details" });
    expect(screen.getByRole("status")).toHaveTextContent("Reception details could not be loaded");
    expect(screen.queryByTestId("reception-summary")).not.toBeInTheDocument();
    await userEvent.click(retry);

    expect(await screen.findByTestId("reception-summary")).toHaveTextContent("Amina Rahman");
    expect(get).toHaveBeenCalledTimes(2);
    expect(get).toHaveBeenLastCalledWith("/v1/sessions/{session_id}/reception", {
      params: { path: { session_id: "ses_retry" } },
    });
    expect(screen.queryByRole("button", { name: "Retry reception details" })).not.toBeInTheDocument();
  });

  it("shows optional requested care in the worklist while keeping the patient identity abbreviated", () => {
    liveWorklist.mockReturnValue({
      state: { status: "ready", data: [
        row("ses_preference", {
          reception_context: RECEPTION,
          state: "queued",
          queue_token: {
            token_id: "tok_waiting", session_id: "ses_preference", token_code: "E-002",
            department: "emergency", status: "waiting", issued_at: RECEPTION.registered_at,
          },
        }),
        row("ses_legacy", { patient_display: "M. S." }),
      ] },
      live: "live",
      refresh: vi.fn(),
    });
    show(<Worklist />);

    const table = screen.getByRole("table");
    const preferred = within(table).getByRole("link", { name: "A. R." }).closest("tr")!;
    expect(preferred).toHaveTextContent("Patient preference");
    expect(preferred).toHaveTextContent("Cardiology");
    expect(preferred).toHaveTextContent("Dr. Layla Hassan");
    expect(preferred).toHaveTextContent("Not booked");
    expect(preferred).toHaveTextContent("Assigned queue: emergency");
    expect(preferred).toHaveTextContent("E-002");
    expect(preferred).toHaveTextContent(RECEPTION.reason);
    expect(within(table).getByRole("link", { name: "M. S." }).closest("tr")).toHaveTextContent("No preference recorded");
    expect(screen.queryByText("Amina Rahman")).not.toBeInTheDocument();
    expect(screen.queryByText("+971500000123")).not.toBeInTheDocument();
    expect(get).not.toHaveBeenCalled();
  });

  it("keeps a caregiver separate from the patient and leaves an adaptive route unassigned", async () => {
    get.mockResolvedValue(reply({
      ...RECEPTION,
      patient: { name: "Mira Example", age_years: 7, sex: "female" },
      caregiver: { name: "Noor Caregiver", phone: "+971500000099", relationship: "parent" },
      visit_for: "child", identity_status: "verified_demo", intake_mode: "adaptive",
      department_id: "unsure", department_name: "To be confirmed by care team",
      doctor_id: undefined, doctor_name: undefined, reason: "", emergency_symptoms: [],
    }));
    show(<SessionReception sessionId="ses_child" />);
    const summary = await screen.findByTestId("reception-summary");
    const patient = within(summary).getByRole("region", { name: "Patient registration" });
    const caregiver = within(summary).getByRole("region", { name: "Accompanying caregiver" });
    expect(patient).toHaveTextContent("Mira Example");
    expect(patient).not.toHaveTextContent("Noor Caregiver");
    expect(caregiver).toHaveTextContent("Noor Caregiver");
    expect(caregiver).toHaveTextContent("Their history is not the patient’s medical record");
    expect(summary).toHaveTextContent("Matched to a demo patient record");
    expect(summary).toHaveTextContent("Care route to be confirmed after assessment");
    expect(summary).toHaveTextContent("Refer to the sourced patient story captured during the conversation");
    expect(within(summary).queryByTestId("reception-preference")).not.toBeInTheDocument();
  });

  it("keeps the assigned emergency/general queue distinct from the Cardiology request", async () => {
    const token: Schema<"QueueToken"> = {
      token_id: "tok_emergency", session_id: "ses_called", token_code: "E-001",
      department: "emergency", status: "called", room: "ED 1", issued_at: RECEPTION.registered_at,
    };
    post.mockResolvedValue(reply({ token: null }));
    show(<QueueControls rows={[
      row("ses_called", { state: "called", queue_token: token, reception_context: RECEPTION }),
      row("ses_waiting", { state: "queued", reception_context: RECEPTION, queue_token: {
        ...token, token_id: "tok_general", session_id: "ses_waiting", token_code: "G-001",
        department: "general", status: "waiting", room: undefined,
      } }),
    ]} onChanged={vi.fn()} />);

    const called = screen.getByTestId("called-token");
    expect(called).toHaveTextContent("Assigned queue: emergency");
    expect(called).toHaveTextContent("Room: ED 1");
    expect(within(called).getByTestId("reception-preference")).toHaveTextContent("Cardiology");
    expect(within(called).getByTestId("reception-preference")).toHaveTextContent("Not booked");
    expect(screen.queryByRole("button", { name: /Call next: Cardiology/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Call next: General" }));
    expect(post).toHaveBeenCalledWith("/v1/queue/{department}/call-next", {
      params: { path: { department: "general" } },
    });
  });
});
