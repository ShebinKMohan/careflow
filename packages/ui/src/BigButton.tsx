import type { ButtonHTMLAttributes } from "react";

export type BigButtonVariant = "primary" | "secondary" | "danger";

const VARIANTS: Record<BigButtonVariant, string> = {
  primary: "bg-primary text-on-primary",
  secondary: "bg-secondary text-on-secondary border-2 border-border",
  danger: "bg-danger text-on-danger",
};

export interface BigButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: BigButtonVariant;
}

/** A button with a touch target of at least 64 x 64 px. */
export function BigButton({ variant = "primary", className = "", type = "button", ...props }: BigButtonProps) {
  return (
    <button
      type={type}
      className={`inline-flex min-h-touch min-w-touch items-center justify-center gap-3 rounded-control px-6 text-title font-semibold disabled:cursor-not-allowed disabled:opacity-60 ${VARIANTS[variant]} ${className}`}
      {...props}
    />
  );
}
