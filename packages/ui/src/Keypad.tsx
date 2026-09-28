"use client";

import { useState } from "react";

export interface KeypadLabels {
  /** Accessible name of the entered-digits display. */
  display: string;
  clear: string;
  submit: string;
}

export interface KeypadProps {
  labels: KeypadLabels;
  onSubmit: (digits: string) => void;
  /** Number of digits to collect (keypad IDs have 6). */
  length?: number;
  disabled?: boolean;
}

const DIGITS = ["1", "2", "3", "4", "5", "6", "7", "8", "9"] as const;
const KEY = "min-h-touch min-w-touch rounded-control border border-border text-display font-semibold shadow-soft transition-colors enabled:hover:bg-secondary-hover enabled:hover:text-ink disabled:cursor-not-allowed disabled:opacity-50";

/**
 * Digits 0-9, clear and submit. The grid is always left-to-right like a phone keypad, also in
 * Arabic. Submit is enabled only when all digits are entered.
 */
export function Keypad({ labels, onSubmit, length = 6, disabled = false }: KeypadProps) {
  const [digits, setDigits] = useState("");
  const complete = digits.length === length;
  const press = (digit: string) => setDigits((current) => (current.length < length ? current + digit : current));

  return (
    <div className="flex flex-col gap-4" dir="ltr">
      <output
        aria-label={labels.display}
        aria-live="polite"
        className="min-h-touch rounded-control border-2 border-border bg-surface px-4 text-center font-mono text-display tracking-[0.3em]"
      >
        {digits.padEnd(length, "·")}
      </output>
      <div className="grid grid-cols-3 gap-3">
        {DIGITS.map((digit) => (
          <button key={digit} type="button" className={`${KEY} bg-surface text-ink`} disabled={disabled} onClick={() => press(digit)}>
            {digit}
          </button>
        ))}
        <button type="button" className={`${KEY} bg-secondary text-on-secondary text-title`} disabled={disabled} onClick={() => setDigits("")}>
          {labels.clear}
        </button>
        <button type="button" className={`${KEY} bg-surface text-ink`} disabled={disabled} onClick={() => press("0")}>
          0
        </button>
        <button
          type="button"
          className={`${KEY} bg-primary text-on-primary text-title`}
          disabled={disabled || !complete}
          onClick={() => onSubmit(digits)}
        >
          {labels.submit}
        </button>
      </div>
    </div>
  );
}
