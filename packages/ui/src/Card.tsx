import type { HTMLAttributes, ReactNode } from "react";

export interface CardProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  title?: ReactNode;
}

export function Card({ title, children, className = "", ...props }: CardProps) {
  return (
    <section className={`rounded-card border-2 border-border bg-surface p-gutter text-ink ${className}`} {...props}>
      {title ? <h2 className="mb-4 text-title font-semibold">{title}</h2> : null}
      {children}
    </section>
  );
}
