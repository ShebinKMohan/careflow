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

import { type ConsentChoices, setConsentChoices } from "@/consent/hasConsent";
import { emptyReceptionDraft, type ReceptionDraft } from "./reception";

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
  window.dispatchEvent(new Event("careflow:device-ready"));
}

export function subscribeDevice(listener: () => void): () => void {
  window.addEventListener("careflow:device-ready", listener);
  return () => window.removeEventListener("careflow:device-ready", listener);
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
  /** Who the patient is (M2-04): a parent checking in a child skips the adult sensor steps. */
  patientType: "adult" | "child";
}

interface KioskContext {
  receptionDraft: ReceptionDraft;
  updateReceptionDraft: (patch: Partial<ReceptionDraft>) => void;
  patient: PatientSession | null;
  /** The consent screen's choices (M2-05), before and after check-in; null until chosen. */
  consent: ConsentChoices | null;
  setConsent: (choices: ConsentChoices) => void;
  start: (result: Schema<"CheckinResult">, fallback: IntakeQuestion) => void;
  setQuestion: (question: IntakeQuestion | null, sessionId?: string) => void;
  setPatientType: (patientType: "adult" | "child") => void;
  /** Forget the patient and stop anything running for them (e.g. a voice stream). */
  wipe: () => void;
  /** Register a clean-up to run on the next wipe; returns the unregister function. */
  onWipe: (cleanup: () => void) => () => void;
}

const Context = createContext<KioskContext | null>(null);

export function KioskProvider({ children }: { children: ReactNode }) {
  const [receptionDraft, setReceptionDraft] = useState<ReceptionDraft>(emptyReceptionDraft);
  const updateReceptionDraft = useCallback((patch: Partial<ReceptionDraft>) => setReceptionDraft((current) => ({ ...current, ...patch })), []);
  const [patient, setPatient] = useState<PatientSession | null>(null);
  const [consent, setConsentState] = useState<ConsentChoices | null>(null);
  const cleanups = useRef(new Set<() => void>());

  const setConsent = useCallback((choices: ConsentChoices) => {
    setConsentChoices(choices);
    setConsentState(choices);
  }, []);

  const start = useCallback((result: Schema<"CheckinResult">, fallback: IntakeQuestion) => {
    setPatient({
      sessionId: result.session_id,
      token: result.session_token,
      display: result.patient_display,
      language: result.language,
      question: result.first_question ?? fallback,
      patientType: "adult",
    });
  }, []);
  const setPatientType = useCallback((patientType: "adult" | "child") => {
    setPatient((current) => (current ? { ...current, patientType } : current));
  }, []);
  const setQuestion = useCallback((question: IntakeQuestion | null, sessionId?: string) => {
    setPatient((current) => (current && (!sessionId || current.sessionId === sessionId) ? { ...current, question } : current));
  }, []);
  const wipe = useCallback(() => {
    for (const cleanup of cleanups.current) cleanup();
    cleanups.current.clear();
    setPatient(null);
    setConsentChoices(null);
    setConsentState(null);
    setReceptionDraft(emptyReceptionDraft());
  }, []);
  const onWipe = useCallback((cleanup: () => void) => {
    cleanups.current.add(cleanup);
    return () => {
      cleanups.current.delete(cleanup);
    };
  }, []);

  const value = useMemo(
    () => ({ patient, consent, setConsent, start, setQuestion, setPatientType, wipe, onWipe, receptionDraft, updateReceptionDraft }),
    [patient, consent, setConsent, start, setQuestion, setPatientType, wipe, onWipe, receptionDraft, updateReceptionDraft],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useKiosk(): KioskContext {
  const context = useContext(Context);
  if (!context) throw new Error("useKiosk needs <KioskProvider>");
  return context;
}
