import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { COMPLAINTS, quickAnswers } from "@/lib/quickAnswers";

// The M1-07 question keys, read from the intake's approved templates.
// Vitest runs in apps/kiosk (jsdom has no file URLs), so the path starts there.
const TEMPLATES = readFileSync(
  resolve(process.cwd(), "../../services/intake/src/careflow_intake/conversation/templates_en.yaml"),
  "utf8",
);
const QUESTION_KEYS = [...(TEMPLATES.split("questions:")[1] ?? "").matchAll(/^ {2}([a-z_.]+):/gm)].map((m) => m[1] ?? "");

describe("quick answers", () => {
  it("offers the complaint grid for the main complaint", () => {
    expect(quickAnswers("chief_complaint")).toEqual({ kind: "choices", answers: COMPLAINTS });
    expect(COMPLAINTS).toEqual(["chestPain", "breathing", "fever", "injury", "pain", "other"]);
  });

  it("offers 0-10 for the pain score and the severity", () => {
    for (const key of ["pain_score", "severity"]) {
      expect(quickAnswers(key)).toEqual({ kind: "scale", min: 0, max: 10 });
    }
  });

  it("offers yes, no and not sure for the screens and yes/no questions", () => {
    for (const key of ["chest_pain.present", "fast.face_droop", "infection.rigors", "child_danger.grunting", "allergies_confirm"]) {
      expect(quickAnswers(key)).toEqual({ kind: "choices", answers: ["yes", "no", "notSure"] });
    }
  });

  it("leaves times and places to the patient's own words", () => {
    for (const key of ["symptom_onset_at", "pain_location", "chest_pain.onset", "fast.last_known_well"]) {
      expect(quickAnswers(key)).toEqual({ kind: "none" });
    }
  });

  it("offers OK for a message with no question", () => {
    expect(quickAnswers(null)).toEqual({ kind: "choices", answers: ["ok"] });
  });

  it("knows every question the intake can ask", () => {
    expect(QUESTION_KEYS).toHaveLength(23);
    const free = new Set(["symptom_onset_at", "pain_location", "chest_pain.onset", "fast.last_known_well"]);
    for (const key of QUESTION_KEYS) {
      expect(quickAnswers(key).kind === "none", key).toBe(free.has(key));
    }
  });
});
