/**
 * Patient scripts more than one journey answers with. The model replies are replayed by prompt
 * (e2e/replays), so a journey that answers the same persona's questions with the same words
 * replays the same recorded replies.
 */
import type { PatientScript } from "./actors";

/** Persona `routine` (keypad 100005, synthetic): a twisted ankle, no danger sign. */
export const ROUTINE_WALK_IN: PatientScript = {
  complaint: "I twisted my ankle playing football yesterday",
  yes: [],
  scale: "4",
  typed: [
    [/when did (this|the pain) start/i, "Yesterday afternoon"],
    [/where is the pain/i, "My left ankle"],
  ],
};
