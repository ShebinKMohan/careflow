import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { Schema } from "@careflow/contracts";
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type IntakeQuestion, KioskProvider, useKiosk } from "@/lib/session";

const CHECKIN = JSON.parse(
  readFileSync(resolve(process.cwd(), "tests/fixtures/api/checkin.json"), "utf8"),
) as Schema<"CheckinResult">;
const FALLBACK: IntakeQuestion = {
  question_id: "q_chief_complaint_kiosk",
  text: "What is the main reason for your visit today?",
  key: "chief_complaint",
  notice: null,
};

function kiosk() {
  return renderHook(() => useKiosk(), { wrapper: KioskProvider }).result;
}

describe("kiosk session", () => {
  it("keeps the patient in memory only; wipe() forgets them and stops their voice stream once", () => {
    const state = kiosk();
    const abort = vi.fn();
    act(() => state.current.start(CHECKIN, FALLBACK));
    act(() => void state.current.onWipe(abort));

    expect(state.current.patient).toMatchObject({ sessionId: "ses_e2e", display: "M. S.", question: CHECKIN.first_question });
    expect(window.sessionStorage.length).toBe(0);
    expect(window.localStorage.length).toBe(0);
    expect(document.cookie).toBe("");

    act(() => state.current.wipe());
    expect(state.current.patient).toBeNull();
    expect(abort).toHaveBeenCalledTimes(1);
    act(() => state.current.wipe());
    expect(abort).toHaveBeenCalledTimes(1);
  });

  it("asks the fallback question when the check-in carries none, and skips an unregistered clean-up", () => {
    const state = kiosk();
    const abort = vi.fn();
    const older = { ...CHECKIN };
    delete older.first_question;
    act(() => state.current.start(older, FALLBACK));
    expect(state.current.patient?.question).toEqual(FALLBACK);

    let unregister = () => {};
    act(() => {
      unregister = state.current.onWipe(abort);
    });
    unregister();
    act(() => state.current.wipe());
    expect(abort).not.toHaveBeenCalled();
  });
});
