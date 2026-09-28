"use client";

import { useState } from "react";

export const KEYPAD_LENGTH = 6;

export interface KeypadLabels {
  /** Accessible name of the masked display. */
  display: string;
  backspace: string;
  clear: string;
  submit: string;
}

export interface KeypadProps {
  labels: KeypadLabels;
  onSubmit: (digits: string) => void;
  disabled?: boolean;
  length?: number;
}

const DIGITS = ["1", "2", "3", "4", "5", "6", "7", "8", "9"] as const;
const KEY = "min-h-touch min-w-touch rounded-control border-2 border-border text-display font-semibold";

/**
 * The silent keypad: digits 0-9, backspace, clear and OK. The entry shows only as dots, and the
 * display opens no on-screen keyboard (`inputMode="none"`). Nothing is spoken on this screen.
 */
export function Keypad({ labels, onSubmit, disabled = false, length = KEYPAD_LENGTH }: KeypadProps) {
  const [digits, setDigits] = useState("");
  const press = (digit: string) => setDigits((current) => (current.length < length ? current + digit : current));

  return (
    <div className="keypad-surface flex flex-col gap-4" dir="ltr">
      <input
        aria-label={labels.display}
        readOnly
        inputMode="none"
        autoComplete="off"
        value={"•".repeat(digits.length)}
        placeholder={"·".repeat(length)}
        className="min-h-touch rounded-control border-2 border-border bg-surface px-4 text-center font-mono text-display tracking-[0.3em]"
      />
      <div className="grid grid-cols-3 gap-3">
        {DIGITS.map((digit) => (
          <button key={digit} type="button" className={`${KEY} bg-surface text-ink`} disabled={disabled} onClick={() => press(digit)}>
            {digit}
          </button>
        ))}
        <button
          type="button"
          aria-label={labels.backspace}
          className={`${KEY} bg-secondary text-on-secondary`}
          disabled={disabled || digits.length === 0}
          onClick={() => setDigits((current) => current.slice(0, -1))}
        >
          ⌫
        </button>
        <button type="button" className={`${KEY} bg-surface text-ink`} disabled={disabled} onClick={() => press("0")}>
          0
        </button>
        <button
          type="button"
          className={`${KEY} bg-secondary text-on-secondary text-title`}
          disabled={disabled || digits.length === 0}
          onClick={() => setDigits("")}
        >
          {labels.clear}
        </button>
      </div>
      <button
        type="button"
        className="min-h-touch rounded-control bg-primary px-6 text-title font-semibold text-on-primary disabled:opacity-60"
        disabled={disabled || digits.length !== length}
        onClick={() => onSubmit(digits)}
      >
        {labels.submit}
      </button>
    </div>
  );
}
