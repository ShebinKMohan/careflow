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

/** The same walk-in in Arabic (M2-01): its replies are recorded like the English ones. */
export const ROUTINE_WALK_IN_AR: PatientScript = {
  complaint: "لويت كاحلي أمس وأنا ألعب كرة القدم",
  yes: [],
  scale: "4",
  typed: [
    [/متى بدأ/, "أمس بعد الظهر"],
    [/أين مكان الألم/, "كاحلي الأيسر"],
  ],
};

/** Persona `chest-pain` (keypad 100001, synthetic): chest pain since the morning. */
export const CHEST_PAIN: PatientScript = {
  complaint: "I've had chest pain since this morning",
  yes: [/pain or tightness in your chest/i],
  scale: "7",
  typed: [
    [/when did (the chest pain|this) start/i, "It started at about seven this morning"],
    [/where is the pain/i, "In the middle of my chest"],
  ],
};

/** The same in Arabic (M2-12's @ar variant). */
export const CHEST_PAIN_AR: PatientScript = {
  complaint: "عندي ألم في صدري من الصبح",
  yes: [/ألم أو ضيق في صدرك/],
  scale: "7",
  typed: [
    [/متى بدأ/, "بدأ حوالي الساعة السابعة صباحاً"],
    [/أين مكان الألم/, "في وسط صدري"],
  ],
};

/** Persona `sepsis` (keypad 100004, synthetic): fever and shivering, with the septic device scenario. */
export const SEPSIS: PatientScript = {
  complaint: "I've had a fever and shivering since yesterday",
  yes: [/fever/i, /shaking or shivering/i],
  scale: "5",
  typed: [[/when did (this|the) .*start/i, "Since yesterday evening"]],
};

