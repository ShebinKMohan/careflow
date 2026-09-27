"use client";

import { useLiveStatus } from "@/lib/liveStatus";

import { AskPanel } from "./AskPanel";
import { FeelsWorse } from "./FeelsWorse";
import { StatusView } from "./StatusView";

/** The waiting room on the phone (M2-07): live status, "ask the clinic", and "I feel worse". */
export function WaitingRoom() {
  const live = useLiveStatus();
  const childSession = live.status === "ready" && live.data.patient_type === "child";
  return (
    <div className="flex flex-col gap-4">
      <StatusView live={live} />
      {live.status === "ready" ? (
        <>
          <AskPanel />
          <FeelsWorse childSession={childSession} />
        </>
      ) : null}
    </div>
  );
}
