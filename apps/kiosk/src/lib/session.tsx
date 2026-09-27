"use client";

/**
 * Kiosk state.
 *
 * - The device sign-in (a kiosk_device token, not patient data) stays in the tab's sessionStorage,
 *   so a reload keeps the kiosk signed in.
 * - The patient's session (token, initials, current question) lives in React state only: never in
 *   sessionStorage, localStorage or a cookie. `wipe()` forgets it (idle timeout, done, start).
 */
import type { Schema } from "@careflow/contracts";
import { createContext, type ReactNode, useCallback, useContext, useMemo, useRef, useState } from "react";

const DEVICE_KEY = "careflow.kiosk.device";

export interface DeviceSession {
  token: string;
  clinicId: string;
  deviceId: string;
}

export function readDevice(): DeviceSession | null {
  try {
    const raw = window.sessionStorage.getItem(DEVICE_KEY);
    return raw ? (JSON.parse(raw) as DeviceSession) : null;
  } catch {
    return null;
  }
}

export function saveDevice(device: DeviceSession): void {
  window.sessionStorage.setItem(DEVICE_KEY, JSON.stringify(device));
}

export type IntakeQuestion = Schema<"IntakeQuestion">;

export interface PatientSession {
  sessionId: string;
  token: string;
  /** Initials only, e.g. "M. S.". */
  display: string;
  language: Schema<"Language">;
  /** The question on screen; null once the conversation is done. */
  question: IntakeQuestion | null;
}

interface KioskContext {
  patient: PatientSession | null;
  start: (result: Schema<"CheckinResult">, fallback: IntakeQuestion) => void;
  setQuestion: (question: IntakeQuestion | null) => void;
  /** Forget the patient and stop anything running for them (e.g. a voice stream). */
  wipe: () => void;
  /** Register a clean-up to run on the next wipe; returns the unregister function. */
  onWipe: (cleanup: () => void) => () => void;
}

const Context = createContext<KioskContext | null>(null);

export function KioskProvider({ children }: { children: ReactNode }) {
  const [patient, setPatient] = useState<PatientSession | null>(null);
  const cleanups = useRef(new Set<() => void>());

  const start = useCallback((result: Schema<"CheckinResult">, fallback: IntakeQuestion) => {
    setPatient({
      sessionId: result.session_id,
      token: result.session_token,
      display: result.patient_display,
      language: result.language,
      question: result.first_question ?? fallback,
    });
  }, []);
  const setQuestion = useCallback((question: IntakeQuestion | null) => {
    setPatient((current) => (current ? { ...current, question } : current));
  }, []);
  const wipe = useCallback(() => {
    for (const cleanup of cleanups.current) cleanup();
    cleanups.current.clear();
    setPatient(null);
  }, []);
  const onWipe = useCallback((cleanup: () => void) => {
    cleanups.current.add(cleanup);
    return () => {
      cleanups.current.delete(cleanup);
    };
  }, []);

  const value = useMemo(() => ({ patient, start, setQuestion, wipe, onWipe }), [patient, start, setQuestion, wipe, onWipe]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useKiosk(): KioskContext {
  const context = useContext(Context);
  if (!context) throw new Error("useKiosk needs <KioskProvider>");
  return context;
}
