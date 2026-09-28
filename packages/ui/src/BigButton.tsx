import type { ButtonHTMLAttributes } from "react";

export type BigButtonVariant = "primary" | "secondary" | "danger";

const VARIANTS: Record<BigButtonVariant, string> = {
  primary: "bg-primary text-on-primary hover:bg-primary-hover active:bg-primary-pressed shadow-soft",
  secondary: "bg-surface text-on-secondary border border-border hover:bg-secondary-hover active:bg-secondary-pressed",
  danger: "bg-danger text-on-danger hover:bg-critical-hover active:bg-critical-pressed",
};

export interface BigButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: BigButtonVariant;
}

/** A button with a touch target of at least 64 x 64 px. */
export function BigButton({ variant = "primary", className = "", type = "button", ...props }: BigButtonProps) {
  return (
    <button
      type={type}
      className={`cf-button inline-flex min-h-touch min-w-touch items-center justify-center gap-3 rounded-control px-6 py-3 text-button font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${VARIANTS[variant]} ${className}`}
      {...props}
    />
  );
}
