import { describe, expect, expectTypeOf, it } from "vitest";

import type { Schema, paths, webhooks } from "../ts/index";

type KeypadPost = paths["/v1/checkin/keypad"]["post"];
type KeypadBody = KeypadPost["requestBody"]["content"]["application/json"];
type KeypadOk = KeypadPost["responses"][200]["content"]["application/json"];

describe("generated contract types", () => {
  it("types the keypad check-in request and response", () => {
    const body: KeypadBody = { clinic_id: "clinic_demo", kiosk_id: "kiosk_01", digits: "100001" };
    const ok: KeypadOk = {
      session_id: "ses_chest_pain",
      session_token: "token",
      patient_display: "M. S.",
      language: "en",
    };
    expect(body.digits).toHaveLength(6);
    expect(ok.language).toBe("en");
    expectTypeOf<KeypadOk>().toEqualTypeOf<Schema<"CheckinResult">>();
    expectTypeOf<KeypadOk["language"]>().toEqualTypeOf<"en" | "ar">();
  });

  it("types the webhooks with their event type", () => {
    type AlertRaised = webhooks["alert.raised"]["post"]["requestBody"]["content"]["application/json"];
    expectTypeOf<AlertRaised["type"]>().toEqualTypeOf<"alert.raised">();
  });

  it("keeps the session status free of levels and alerts", () => {
    type Status = Schema<"SessionStatus">;
    expectTypeOf<keyof Status>().toEqualTypeOf<"state" | "token_code" | "room" | "wait_estimate_min" | "vitals">();
    // The kiosk vitals panel (M1-03) gets measured numbers only.
    expectTypeOf<keyof Schema<"LatestVitals">>().toEqualTypeOf<
      "heart_rate" | "spo2" | "temperature_c" | "systolic_bp" | "diastolic_bp" | "measured_at" | "device_id" | "simulated"
    >();
  });
});
