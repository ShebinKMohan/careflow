import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MobileStart } from "@/components/MobileStart";
import { MESSAGES } from "@/i18n/messages";

const { post, get } = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }));
vi.mock("@careflow/contracts/client", () => ({ createCareflowClient: () => ({ POST: post, GET: get }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn(), replace: vi.fn() }) }));
const DIRECTORY = { hospital_name: "Northstar General Hospital", demo: true, notice: "Demo directory", departments: [{ id: "general_medicine", name: "General Medicine", description: "General care", doctors: [] }] };
const VERIFIED = { verification_token: "proof-for-adult", patient: { name: "Demo Returning Adult", age_years: 58, sex: "female", phone: "+440000000000" }, history: { status: "partial", allergies: ["Example allergy"], conditions: [], medications: [] }, demo: true };
function show() { return render(<StrictMode><NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC"><MobileStart /></NextIntlClientProvider></StrictMode>); }
beforeEach(() => {
  post.mockReset(); get.mockReset(); get.mockResolvedValue({ data: DIRECTORY });
  window.sessionStorage.clear(); window.history.replaceState(null, "", "/start#t=secure-mobile-start-token");
});
async function next() { await userEvent.click(screen.getByRole("button", { name: "Continue" })); }
async function patient(name = "Demo Example", age = "34") {
  await userEvent.type(screen.getByLabelText("Patient’s full name"), name);
  await userEvent.type(screen.getByLabelText("Age in years"), age);
  await userEvent.selectOptions(screen.getByLabelText("Sex", { exact: true }), "female");
}
async function newPatient() {
  await userEvent.click(await screen.findByRole("button", { name: "Register as a new patient" }));
  await userEvent.click(screen.getByRole("button", { name: /Myself/ })); await next(); await patient();
}
async function returningPatient() {
  post.mockResolvedValueOnce({ data: { status: "match", challenge_token: "masked-challenge", masked_name: "D. R.", demo: true, message: "Verify" } });
  await userEvent.click(await screen.findByRole("button", { name: "Patient ID" }));
  await userEvent.type(screen.getByLabelText("Patient ID", { exact: true }), "100001");
  expect(screen.getByRole("button", { name: "Find my record" })).toBeDisabled();
  await userEvent.click(screen.getByRole("checkbox", { name: "I agree to use these details to find my patient record." })); await userEvent.click(screen.getByRole("button", { name: "Find my record" }));
  expect(await screen.findByText("D. R.")).toBeInTheDocument();
  expect(screen.queryByText(VERIFIED.patient.name)).not.toBeInTheDocument();
  post.mockResolvedValueOnce({ data: VERIFIED });
  await userEvent.type(screen.getByLabelText("Year of birth"), "1968"); await userEvent.click(screen.getByRole("button", { name: "Verify my details" }));
  await screen.findByText(/Verified as Demo Returning Adult/);
}
async function consent() {
  await next(); await next();
  expect(screen.getByRole("button", { name: "Start my check-in" })).toBeDisabled();
  await userEvent.click(screen.getByRole("checkbox", { name: "I agree to use this patient’s details and answers for this visit." }));
}

describe("phone identity and adaptive check-in", () => {
  it("sends an urgent nurse request before identity or patient details and retries without a second visit", async () => {
    post.mockRejectedValue(new TypeError("Lost response")); show();
    await userEvent.click(await screen.findByText("Get nurse help without finishing check-in"));
    expect(screen.getByRole("button", { name: "Request nurse help now" })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "I agree to share this help request with the care team." }));
    await userEvent.click(screen.getByRole("button", { name: "Request nurse help now" }));
    await screen.findByText("Your request is not confirmed yet. Retry the same request or tell reception immediately.");
    await userEvent.click(screen.getByRole("button", { name: "Retry this help request" }));
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls[0]?.[0]).toBe("/v1/checkin/urgent");
    expect(post.mock.calls[0]?.[1].body).toEqual({ qr_token: "secure-mobile-start-token", claim_key: expect.stringMatching(/^[A-Za-z0-9_-]{43}$/), language: "en", patient_type: "adult", emergency_symptoms: [], consent: { camera: false, voice: false, data_use: true, consent_version: "2026-09-28.1" } });
    expect(post.mock.calls[1]?.[1].body).toEqual(post.mock.calls[0]?.[1].body);
    expect(sessionStorage.length).toBe(0);
  });
  it("starts without a lookup and preserves patient details across Back and recipient selection", async () => {
    show(); await newPatient();
    expect(window.location.hash).toBe(""); expect(post).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    await userEvent.click(screen.getByRole("button", { name: /Myself/ })); await next();
    expect(screen.getByLabelText("Patient’s full name")).toHaveValue("Demo Example");
    expect(screen.getByLabelText("Age in years")).toHaveValue(34);
  });
  it("starts actual adaptive intake with no department requirement and retries the same body after a lost response", async () => {
    post.mockRejectedValue(new TypeError("Offline")); show(); await newPatient(); await consent();
    await userEvent.click(screen.getByText("Need urgent help right now?"));
    await userEvent.click(within(screen.getByText("Need urgent help right now?").closest("details")!).getByRole("checkbox", { name: "Chest pain" }));
    await userEvent.click(screen.getByRole("button", { name: "Alert the nurse and start" })); await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "Back" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Retry opening my visit" }));
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls[0]?.[0]).toBe("/v1/checkin/adaptive");
    expect(post.mock.calls[0]?.[1].body).toEqual({ qr_token: "secure-mobile-start-token", claim_key: expect.stringMatching(/^[A-Za-z0-9_-]{43}$/), language: "en", visit_for: "self", patient: { name: "Demo Example", age_years: 34, sex: "female" }, department_id: "unsure", emergency_symptoms: ["chest_pain"], consent: { camera: false, voice: false, data_use: true, consent_version: "2026-09-28.1" } });
    expect(post.mock.calls[1]?.[1].body).toEqual(post.mock.calls[0]?.[1].body);
    expect(sessionStorage.length).toBe(0);
  });
  it("reveals verified history only after birth-year verification and links only the patient's identity", async () => {
    show(); await returningPatient();
    expect(post.mock.calls[0]?.[1].body.consent.data_use).toBe(true);
    expect(post.mock.calls[1]?.[1].body).toMatchObject({ birth_year: 1968, challenge_token: "masked-challenge", qr_token: "secure-mobile-start-token" });
    await userEvent.click(screen.getByRole("button", { name: /Myself/ })); await next();
    expect(screen.getByLabelText("Patient’s full name")).toHaveValue(VERIFIED.patient.name);
    expect(screen.getByText("Example allergy")).toBeInTheDocument();
    expect(screen.getByText(/Some record information is unavailable/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Confirm these details" })); await next();
    await userEvent.click(screen.getByRole("checkbox", { name: "I agree to use this patient’s details and answers for this visit." }));
    post.mockRejectedValue(new TypeError("Offline")); await userEvent.click(screen.getByRole("button", { name: "Start my check-in" }));
    expect(post.mock.calls[2]?.[1].body).toMatchObject({ patient_identity_token: "proof-for-adult", patient: VERIFIED.patient, visit_for: "self" });
    expect(post.mock.calls[2]?.[1].body.caregiver).toBeUndefined();
    expect(sessionStorage.length).toBe(0);
  });
  it("keeps the verified adult as caregiver and never copies their profile or history into a child's visit", async () => {
    show(); await returningPatient(); await userEvent.click(screen.getByRole("button", { name: /A child/ })); await next();
    expect(screen.getByLabelText("Patient’s full name")).toHaveValue("");
    expect(screen.queryByText("Example allergy")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Your full name")).toHaveValue(VERIFIED.patient.name);
    await patient("Demo Child", "5"); await consent();
    post.mockRejectedValue(new TypeError("Offline")); await userEvent.click(screen.getByRole("button", { name: "Start my check-in" }));
    const body = post.mock.calls[2]?.[1].body;
    expect(body).toMatchObject({ visit_for: "child", patient: { name: "Demo Child", age_years: 5, sex: "female" }, caregiver: { name: VERIFIED.patient.name, relationship: "parent", identity_token: "proof-for-adult" } });
    expect(body.patient_identity_token).toBeUndefined(); expect(body.patient.phone).toBeUndefined();
  });
  it("shows an expired QR as an error, without pretending a visit was opened", async () => {
    post.mockResolvedValue({ response: { status: 401 } }); show(); await newPatient(); await consent();
    await userEvent.click(screen.getByRole("button", { name: "Start my check-in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("QR code has expired");
    expect(screen.getByRole("button", { name: "Start my check-in" })).toBeDisabled(); expect(sessionStorage.length).toBe(0);
  });
});
