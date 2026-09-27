/** Under 2 minutes left the countdown turns red. */
export const URGENT_MS = 2 * 60 * 1000;

export interface Countdown {
  /** "mm:ss" (h:mm:ss from an hour) left before the deadline, or elapsed since it when overdue. */
  time: string;
  overdue: boolean;
  urgent: boolean;
}

const pad = (value: number) => String(value).padStart(2, "0");

/** mm:ss, or h:mm:ss from an hour up. */
function clock(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${pad(minutes)}:${pad(seconds)}`;
}

/** The time left to `deadline` (rounded up), or how long it is overdue (rounded down). */
export function countdown(deadline: number, now: number): Countdown {
  const left = deadline - now;
  if (left >= 0) {
    return { time: clock(Math.ceil(left / 1000)), overdue: false, urgent: left < URGENT_MS };
  }
  return { time: clock(Math.floor(-left / 1000)), overdue: true, urgent: true };
}
