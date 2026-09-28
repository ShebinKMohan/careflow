import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { Schema } from "@careflow/contracts";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { type ReactNode, useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CheckinFlow } from "@/components/CheckinFlow";
import { CONSENT_VERSION, type ConsentChoices, hasConsent, markConsentSaved, setConsentChoices } from "@/consent/hasConsent";
import { cardReader } from "@/devices/card-reader";
import { MOCK_CARDS } from "@/devices/mockCards";
import { MESSAGES } from "@/i18n/messages";
import { KioskProvider, saveDevice, useKiosk } from "@/lib/session";

// M2-05: the kiosk carries on only once the API has saved the patient's consent choices, and the
// microphone and camera count as consented only then. An API that cannot be reached at check-in
// is said so, and never counted as a failed attempt.

// The kiosk's device client is built when its module loads, so fetch is replaced before any import.
const { router, fetchMock } = vi.hoisted(() => {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  return { router: { push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }, fetchMock };
});
vi.mock("next/navigation", () => ({ useRouter: () => router }));

const CHECKIN = JSON.parse(
  readFileSync(resolve(process.cwd(), "tests/fixtures/api/checkin.json"), "utf8"),
) as Schema<"CheckinResult">;
const AGREE: ConsentChoices = { camera: true, voice: true, dataUse: true };
const T = MESSAGES.en.safety.consent;

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const saved = (body: Record<string, unknown>) =>
  reply(200, { session_id: CHECKIN.session_id, recorded_at: "2026-09-28T09:00:00Z", ...body });

/** The API: the keypad or the card checks the persona in (each check-in gets the next of
 * `checkinAnswers`, then succeeds); each consent POST gets the next of `consentAnswers`. */
function stubApi(consentAnswers: Array<Response | Error>, checkinAnswers: Array<Response | Error> = []) {
  const bodies: Record<string, unknown>[] = [];
  fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init);
    const path = new URL(request.url).pathname;
    if (path === "/v1/checkin/keypad" || path === "/v1/checkin/id-tap") {
      const answer = checkinAnswers.shift();
      if (answer instanceof Error) throw answer;
      return answer ?? reply(200, CHECKIN);
    }
    if (path === `/v1/sessions/${CHECKIN.session_id}/consent`) {
      const body = (await request.json()) as Record<string, unknown>;
      bodies.push(body);
      const answer = consentAnswers.shift();
      if (answer instanceof Error) throw answer;
      return answer ?? saved(body);
    }
    return reply(404, {});
  });
  return bodies;
}

const seen: { current: ReturnType<typeof useKiosk> | null } = { current: null };
function Chose({ choices, children }: { choices: ConsentChoices; children: ReactNode }) {
  const kiosk = useKiosk();
  useEffect(() => {
    seen.current = kiosk;
  });
  useEffect(() => kiosk.setConsent(choices), []); // eslint-disable-line react-hooks/exhaustive-deps -- once, as the consent screen does
  return kiosk.consent ? <>{children}</> : null;
}

function renderCheckin(choices: ConsentChoices) {
  render(
    <NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC">
      <KioskProvider>
        <Chose choices={choices}>
          <CheckinFlow />
        </Chose>
      </KioskProvider>
    </NextIntlClientProvider>,
  );
}

async function checkInWith(choices: ConsentChoices) {
  renderCheckin(choices);
  for (const digit of "100001") await userEvent.click(await screen.findByRole("button", { name: digit }));
  await userEvent.click(screen.getByRole("button", { name: MESSAGES.en.checkin.submit }));
}

beforeEach(() => {
  vi.clearAllMocks();
  seen.current = null;
  window.sessionStorage.clear();
  saveDevice({ token: "kiosk-device-token", clinicId: "clinic_demo", deviceId: "kiosk_01" });
});
afterEach(() => {
  fetchMock.mockReset();
  setConsentChoices(null);
});

describe("kiosk consent", () => {
  it("counts choices only once the API has saved them", () => {
    setConsentChoices(AGREE);
    expect(hasConsent("voice")).toBe(false);
    expect(hasConsent("camera")).toBe(false);
    markConsentSaved();
    expect(hasConsent("voice")).toBe(true);
    setConsentChoices({ camera: false, voice: false, dataUse: true }); // new choices: unsaved again
    markConsentSaved();
    expect(hasConsent("voice")).toBe(false);
    setConsentChoices({ camera: true, voice: true, dataUse: false });
    markConsentSaved();
    expect(hasConsent("voice")).toBe(false); // no data use, no microphone
    setConsentChoices(null);
    markConsentSaved();
    expect(hasConsent("camera")).toBe(false);
  });

  it("saves the choices with their text version, then opens the intake", async () => {
    const bodies = stubApi([]);
    await checkInWith(AGREE);
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/patient"));
    expect(bodies).toEqual([{ camera: true, voice: true, data_use: true, consent_version: CONSENT_VERSION }]);
    expect(hasConsent("voice")).toBe(true);
    expect(screen.queryByTestId("consent-not-saved")).not.toBeInTheDocument();
  });

  it.each([
    ["an error answer", reply(500, { title: "Internal Server Error" })],
    ["no answer at all", new TypeError("Failed to fetch")],
  ])("never carries on as if saved after %s, and saves on a retry", async (_case, failure) => {
    const bodies = stubApi([failure]);
    await checkInWith(AGREE);
    expect(await screen.findByTestId("consent-not-saved")).toHaveTextContent(T.saveFailed);
    expect(router.push).not.toHaveBeenCalledWith("/patient");
    expect(hasConsent("voice")).toBe(false); // the microphone stays off
    expect(hasConsent("camera")).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: T.retry }));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/patient"));
    expect(bodies).toHaveLength(2);
    expect(hasConsent("voice")).toBe(true);
  });

  it("keeps saying so while the save keeps failing", async () => {
    stubApi([reply(503, {}), reply(503, {})]);
    await checkInWith(AGREE);
    await screen.findByTestId("consent-not-saved");
    await userEvent.click(screen.getByRole("button", { name: T.retry }));
    await waitFor(() => expect(screen.getByRole("button", { name: T.retry })).toBeEnabled());
    expect(screen.getByTestId("consent-not-saved")).toBeInTheDocument();
    expect(router.push).not.toHaveBeenCalledWith("/patient");
    expect(hasConsent("voice")).toBe(false);
  });

  it("starts again from the welcome screen, forgetting the patient and the choices", async () => {
    stubApi([reply(500, {})]);
    await checkInWith(AGREE);
    await screen.findByTestId("consent-not-saved");
    await userEvent.click(screen.getByRole("button", { name: T.startOver }));
    expect(router.push).toHaveBeenCalledWith("/");
    await waitFor(() => expect(seen.current?.patient).toBeNull());
    expect(seen.current?.consent).toBeNull();
    expect(router.replace).not.toHaveBeenCalledWith("/consent"); // home, not back to consent
    expect(hasConsent("voice")).toBe(false);
  });

  it("records a decline of camera and voice, and then the microphone stays off", async () => {
    const bodies = stubApi([]);
    await checkInWith({ camera: false, voice: false, dataUse: true });
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/patient"));
    expect(bodies[0]).toMatchObject({ camera: false, voice: false, data_use: true });
    expect(hasConsent("voice")).toBe(false);
    expect(hasConsent("camera")).toBe(false);
  });
});

describe("kiosk check-in without the API", () => {
  const C = MESSAGES.en.checkin;

  it("says the keypad could not reach the API, counts no failed attempt, and checks in on a retry", async () => {
    const bodies = stubApi([], [new TypeError("Failed to fetch")]);
    await checkInWith(AGREE);
    expect(await screen.findByTestId("checkin-unreachable")).toHaveTextContent(C.unreachable);
    expect(screen.queryByText(C.failed)).not.toBeInTheDocument(); // not "no match"
    expect(router.push).not.toHaveBeenCalled();
    expect(bodies).toEqual([]); // no session, so nothing to record consent for

    const submit = screen.getByRole("button", { name: C.submit }); // enabled again, digits kept
    await userEvent.click(submit);
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/patient"));
    expect(screen.queryByTestId("checkin-unreachable")).not.toBeInTheDocument();
    expect(bodies).toHaveLength(1);
  });

  it("says a card tap could not reach the API, and checks in on the next tap", async () => {
    const bodies = stubApi([], [new TypeError("Failed to fetch")]);
    renderCheckin(AGREE);
    await screen.findByRole("button", { name: C.submit });
    const { card } = MOCK_CARDS[0]!;
    cardReader.tap(card);
    expect(await screen.findByTestId("checkin-unreachable")).toHaveTextContent(C.unreachable);
    expect(screen.queryByTestId("card-message")).not.toBeInTheDocument(); // not "no record"
    expect(router.push).not.toHaveBeenCalled();
    cardReader.tap(card);
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/patient"));
    expect(bodies).toHaveLength(1);
  });
});
