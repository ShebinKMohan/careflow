"use client";

import { useLiveStatus } from "@/lib/liveStatus";

import { AskPanel } from "./AskPanel";
import { FeelsWorse } from "./FeelsWorse";
import { StatusView } from "./StatusView";
import { PatientReceipt } from "./PatientReceipt";
import { RegistrationSummary } from "./RegistrationSummary";

/**
 * The waiting room on the phone (M2-07): live status, "ask the clinic", and "I feel worse". The
 * safety button never depends on the status loading: if the phone cannot reach the nurse, pressing
 * it tells the patient to go to the reception desk.
 */
export function WaitingRoom() {
  const live = useLiveStatus();
  const childSession = live.status === "ready" && live.data.patient_type === "child";
  return (
    <div className="companion-waiting-room">
      {live.status === "ready" ? <RegistrationSummary /> : null}
      <StatusView live={live} />
      {live.status === "ready" ? <PatientReceipt refreshKey={`${live.data.state}:${live.data.token?.token_id ?? ""}`} /> : null}
      {live.status === "ready" ? <AskPanel /> : null}
      <FeelsWorse childSession={childSession} />
    </div>
  );
}
