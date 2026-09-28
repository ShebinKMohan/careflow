import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { Schema } from "@careflow/contracts";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { type ReactNode, useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ContinueOnPhone } from "@/components/ContinueOnPhone";
import { RevokedGuard } from "@/components/RevokedGuard";
import { MESSAGES } from "@/i18n/messages";
import { PATIENT_REVOKED, patientApi } from "@/lib/api";
import { type IntakeQuestion, KioskProvider, useKiosk } from "@/lib/session";

const { router, watchSession, frames } = vi.hoisted(() => ({
  router: { push: vi.fn(), replace: vi.fn(), refresh: vi.fn() },
  watchSession: vi.fn(),
  frames: { deliver: (() => undefined) as (type: string) => void, stops: 0 },
}));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/sessionChannel", () => ({ watchSession }));

const CHECKIN = JSON.parse(
  readFileSync(resolve(process.cwd(), "tests/fixtures/api/checkin.json"), "utf8"),
) as Schema<"CheckinResult">;
const FALLBACK: IntakeQuestion = {
  question_id: "q_chief_complaint_kiosk",
  text: "What is the main reason for your visit today?",
  key: "chief_complaint",
  notice: null,
};

const seen: { current: ReturnType<typeof useKiosk> | null } = { current: null };
function Patient({ children }: { children?: ReactNode }) {
  const kiosk = useKiosk();
  const { start, patient } = kiosk;
  useEffect(() => {
    seen.current = kiosk;
  });
  useEffect(() => {
    if (!patient) start(CHECKIN, FALLBACK);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- once, like a check-in
  return patient ? <>{children}</> : null;
}

function show(children?: ReactNode) {
  return render(
    <NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC">
      <KioskProvider>
        <Patient>{children}</Patient>
        <RevokedGuard />
      </KioskProvider>
    </NextIntlClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  seen.current = null;
  frames.stops = 0;
  watchSession.mockImplementation(async (_token: string, _session: string, onFrame: (type: string) => void) => {
    frames.deliver = onFrame;
    return () => {
      frames.stops += 1;
    };
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("kiosk after the QR hand-over", () => {
  it("forgets the patient when the API refuses their token, and ignores an earlier patient's", async () => {
    show();
    await waitFor(() => expect(seen.current?.patient?.sessionId).toBe(CHECKIN.session_id));
    act(() => void window.dispatchEvent(new CustomEvent(PATIENT_REVOKED, { detail: "someone-before" })));
    expect(seen.current?.patient).not.toBeNull();
    expect(router.push).not.toHaveBeenCalled();

    act(() => void window.dispatchEvent(new CustomEvent(PATIENT_REVOKED, { detail: CHECKIN.session_token })));
    expect(seen.current?.patient).toBeNull();
    expect(router.push).toHaveBeenCalledWith("/");
  });

  it("reports a 401 on the patient's calls, and nothing else", async () => {
    const heard = vi.fn();
    window.addEventListener(PATIENT_REVOKED, heard);
    const statuses = [401, 403, 200];
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: statuses.shift() ?? 200, headers: { "content-type": "application/json" } })));
    for (let call = 0; call < 3; call += 1) {
      await patientApi("patient-token").GET("/v1/sessions/{session_id}/status", { params: { path: { session_id: "ses_e2e" } } });
    }
    window.removeEventListener(PATIENT_REVOKED, heard);
    expect(heard).toHaveBeenCalledTimes(1);
    expect((heard.mock.calls[0]?.[0] as CustomEvent<string>).detail).toBe("patient-token");
  });

  it("keeps listening for the claim after the patient closes the code, then forgets them", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ qr_token: "qr-one-time-code-000001", expires_at: new Date(Date.now() + 120_000).toISOString() }),
      { status: 200, headers: { "content-type": "application/json" } },
    )));
    show(<ContinueOnPhone />);
    await userEvent.click(await screen.findByTestId("continue-on-phone"));
    await screen.findByTestId("handover-qr");
    await waitFor(() => expect(watchSession).toHaveBeenCalledTimes(1));

    // "Stay on this screen": the code on screen may still be scanned, so the kiosk keeps listening.
    await userEvent.click(screen.getByRole("button", { name: MESSAGES.en.intake.phone.stay }));
    expect(screen.queryByTestId("handover-qr")).not.toBeInTheDocument();
    expect(frames.stops).toBe(0);

    act(() => frames.deliver("session.handed_over"));
    expect(await screen.findByTestId("handed-over")).toBeInTheDocument();
    // `wipe()` and `router.push("/")` run in one timer callback: the push is recorded at once, the
    // wipe when React next commits (and `seen` is read after that commit). Wait for both.
    await waitFor(
      () => {
        expect(router.push).toHaveBeenCalledWith("/");
        expect(seen.current?.patient).toBeNull();
      },
      { timeout: 7_000 },
    );
    expect(frames.stops).toBe(1);
  }, 10_000);
});
