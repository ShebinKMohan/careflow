import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { useEffect } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ReceptionWizard } from "@/components/ReceptionWizard";
import { MESSAGES } from "@/i18n/messages";
import { KioskProvider, saveDevice, useKiosk } from "@/lib/session";

const { post, get, push, replace } = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn(), push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, replace }) }));
vi.mock("@/lib/api", () => ({ API_BASE_URL: "http://clinic.test", api: { POST: (...args: unknown[]) => post(...args) } }));
vi.mock("@careflow/contracts/client", () => ({ createCareflowClient: () => ({ GET: (...args: unknown[]) => get(...args) }) }));

const result = JSON.parse(readFileSync(resolve(process.cwd(), "tests/fixtures/api/checkin.json"), "utf8"));
const profile = { name: "Demo Parent", age_years: 58, sex: "male", phone: "+971500000001" };
const history = { status: "ok", allergies: ["Penicillin"], medications: ["Parent medicine"], conditions: ["Parent condition"] };
const seen: { current: ReturnType<typeof useKiosk> | null } = { current: null };
function WithConsent() {
  const kiosk = useKiosk();
  const { setConsent } = kiosk;
  useEffect(() => { seen.current = kiosk; });
  useEffect(() => setConsent({ camera: false, voice: false, dataUse: true }), [setConsent]);
  return <ReceptionWizard />;
}
function show() { render(<NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC"><KioskProvider><WithConsent /></KioskProvider></NextIntlClientProvider>); }

beforeEach(() => {
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  seen.current = null;
  post.mockReset().mockImplementation(async (route: string) => route === "/v1/identity/lookup" ? { data: { status: "match", challenge_token: "challenge", masked_name: "D*** P***", demo: true } } : route === "/v1/identity/verify" ? { data: { patient: profile, verification_token: "verified-parent", history, demo: true } } : { data: result });
  get.mockReset().mockResolvedValue({ data: { departments: [] } }); push.mockClear(); replace.mockClear();
  sessionStorage.clear(); saveDevice({ token: "server-managed", clinicId: "clinic_demo", deviceId: "kiosk_demo" });
});

async function identifyReturning(name = "Demo Parent") {
  await userEvent.click(screen.getByRole("button", { name: "Patient ID" }));
  await userEvent.type(screen.getByLabelText("Patient ID"), "100001");
  expect(screen.getByRole("button", { name: "Find my details" })).toBeDisabled();
  await userEvent.click(screen.getByRole("checkbox", { name: MESSAGES.en.reception.lookupConsent }));
  await userEvent.click(screen.getByRole("button", { name: "Find my details" }));
  await screen.findByText("D*** P***");
  expect(screen.queryByText("Demo Parent")).not.toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("Your birth year"), "1968");
  await userEvent.click(screen.getByRole("button", { name: "Verify my details" }));
  await screen.findByText(`Welcome back, ${name}`);
}

async function startConversation() {
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  await userEvent.click(screen.getByRole("button", { name: "Start conversation" }));
  await waitFor(() => expect(push).toHaveBeenCalledWith("/intake"));
}

describe("identity-led adaptive intake", () => {
  it("registers a new patient without lookup or a mandatory department and preserves Back values", async () => {
    show();
    await userEvent.click(screen.getByRole("button", { name: MESSAGES.en.reception.newPatient }));
    await userEvent.click(screen.getByRole("button", { name: "Me" }));
    await userEvent.type(screen.getByLabelText("Full name"), "Alex Morgan");
    await userEvent.type(screen.getByLabelText("Age in years"), "34");
    await userEvent.click(screen.getByRole("button", { name: "Male" }));
    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    await userEvent.click(screen.getByRole("button", { name: "Me" }));
    expect(screen.getByLabelText("Full name")).toHaveValue("Alex Morgan");
    expect(screen.getByLabelText("Age in years")).toHaveValue(34);
    expect(post).not.toHaveBeenCalled();
    await startConversation();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith("/v1/checkin/adaptive", expect.objectContaining({ body: expect.objectContaining({ patient: { name: "Alex Morgan", age_years: 34, sex: "male" }, visit_for: "self", department_id: "unsure" }) }));
    expect(seen.current?.patient?.question).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Return to my visit" }));
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("requires record consent and birth-year verification before prefilling a returning patient", async () => {
    show(); await identifyReturning();
    await userEvent.click(screen.getByRole("button", { name: "Me" }));
    expect(screen.getByLabelText("Full name")).toHaveValue("Demo Parent");
    expect(screen.getByLabelText("Full name")).toHaveAttribute("readonly");
    expect(seen.current?.receptionDraft.history).toEqual(history);
    await startConversation();
    const submitted = post.mock.calls.find(([route]) => route === "/v1/checkin/adaptive")?.[1].body;
    expect(submitted.patient_identity_token).toBe("verified-parent");
    expect(submitted.caregiver).toBeUndefined();
  });

  it.each([
    ["child", "My child", 7, "parent"],
    ["elder", "An older relative", 76, "family"],
    ["dependent", "Someone I care for", 25, "carer"],
  ] as const)("keeps the helper's identity and clinical history separate for a %s", async (visitFor, label, age, relationship) => {
    show(); await identifyReturning();
    await userEvent.click(screen.getByRole("button", { name: label }));
    expect(screen.getByLabelText("Patient’s full name")).toHaveValue("");
    expect(screen.getByLabelText("Your full name (helper)")).toHaveValue("Demo Parent");
    expect(seen.current?.receptionDraft.history).toBeNull();
    expect(seen.current?.receptionDraft.patientIdentityToken).toBe("");
    await userEvent.type(screen.getByLabelText("Patient’s full name"), "Demo Patient");
    await userEvent.type(screen.getByLabelText("Age in years"), String(age));
    await userEvent.click(screen.getByRole("button", { name: "Female" }));
    await startConversation();
    const submitted = post.mock.calls.find(([route]) => route === "/v1/checkin/adaptive")?.[1].body;
    expect(submitted.patient).toEqual({ name: "Demo Patient", age_years: age, sex: "female" });
    expect(submitted.patient_identity_token).toBeUndefined();
    expect(submitted.visit_for).toBe(visitFor);
    expect(submitted.caregiver).toMatchObject({ name: "Demo Parent", identity_token: "verified-parent", relationship });
    expect(seen.current?.receptionDraft.history).toBeNull();
  });

  it("uses a verified child's own record while requiring a separate adult helper", async () => {
    post.mockImplementation(async (route: string) => route === "/v1/identity/lookup"
      ? { data: { status: "match", challenge_token: "challenge", masked_name: "D*** C***", demo: true } }
      : route === "/v1/identity/verify"
        ? { data: { patient: { name: "Demo Child", age_years: 7, sex: "female" }, verification_token: "verified-child", history: { status: "ok", allergies: ["Child allergy"], medications: [], conditions: [] }, demo: true } }
        : { data: result });
    show();
    await userEvent.click(screen.getByRole("button", { name: "Patient ID" }));
    await userEvent.type(screen.getByLabelText("Patient ID"), "100003");
    await userEvent.click(screen.getByRole("checkbox", { name: MESSAGES.en.reception.lookupConsent }));
    await userEvent.click(screen.getByRole("button", { name: "Find my details" }));
    await screen.findByText("D*** C***");
    await userEvent.type(screen.getByLabelText("Your birth year"), "2019");
    await userEvent.click(screen.getByRole("button", { name: "Verify my details" }));
    await screen.findByText("Welcome back, Demo Child");
    await userEvent.click(screen.getByRole("button", { name: "My child" }));
    expect(screen.getByLabelText("Patient’s full name")).toHaveValue("Demo Child");
    expect(screen.getByLabelText("Patient’s full name")).toHaveAttribute("readonly");
    expect(screen.getByLabelText("Your full name (helper)")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Your full name (helper)"), "New Parent");
    await startConversation();
    const submitted = post.mock.calls.find(([route]) => route === "/v1/checkin/adaptive")?.[1].body;
    expect(submitted.patient_identity_token).toBe("verified-child");
    expect(submitted.caregiver).toEqual({ name: "New Parent", relationship: "parent" });
    expect(seen.current?.receptionDraft.history?.allergies).toEqual(["Child allergy"]);
  });

  it("saves an anonymous urgent request only after consent, then continues without a questionnaire", async () => {
    post.mockResolvedValue({ data: { ...result, first_question: null } });
    show();
    await userEvent.click(screen.getByRole("button", { name: MESSAGES.en.reception.urgentHelp }));
    const request = screen.getByRole("button", { name: "Request urgent help now" });
    expect(request).toBeDisabled();
    expect(screen.getByText(MESSAGES.en.reception.helpNotSent)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("checkbox", { name: MESSAGES.en.reception.urgentConsent }));
    await userEvent.click(request);
    await waitFor(() => expect(push).toHaveBeenCalledWith("/vitals"));
    const body = post.mock.calls[0]?.[1].body;
    expect(post.mock.calls[0]?.[0]).toBe("/v1/checkin/urgent");
    expect(body).toMatchObject({ patient_type: "adult", emergency_symptoms: [], consent: { camera: false, voice: false, data_use: true } });
    expect(body.patient).toBeUndefined();
    expect(body.patient_identity_token).toBeUndefined();
    expect(seen.current?.patient?.question).toBeNull();
    expect(seen.current?.receptionDraft.urgentRequest).toBe(true);
  });

  it("retains urgent symptoms after a failed request and records a child's request on retry", async () => {
    post.mockResolvedValueOnce({ error: { detail: "unavailable" } }).mockResolvedValue({ data: { ...result, first_question: null } });
    show();
    await userEvent.click(screen.getByRole("button", { name: MESSAGES.en.reception.urgentHelp }));
    await userEvent.click(screen.getByRole("button", { name: "A child under 16" }));
    await userEvent.click(screen.getByRole("checkbox", { name: MESSAGES.en.reception.symptoms.short_of_breath }));
    await userEvent.click(screen.getByRole("checkbox", { name: MESSAGES.en.reception.urgentConsent }));
    await userEvent.click(screen.getByRole("button", { name: "Request urgent help now" }));
    await screen.findByText(MESSAGES.en.reception.helpFailed);
    expect(seen.current?.receptionDraft.urgentRequest).toBe(false);
    expect(screen.getByRole("checkbox", { name: MESSAGES.en.reception.symptoms.short_of_breath })).toBeChecked();
    expect(push).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Request urgent help now" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/done"));
    expect(post.mock.calls[1]?.[1].body).toMatchObject({ patient_type: "child", emergency_symptoms: ["short_of_breath"] });
    expect(seen.current?.patient?.patientType).toBe("child");
  });
});
