import type { HTMLAttributes, ReactNode } from "react";

export interface CardProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  title?: ReactNode;
}

export function Card({ title, children, className = "", ...props }: CardProps) {
  return (
    <section className={`cf-panel rounded-card border border-line bg-surface p-gutter text-ink shadow-soft ${className}`} {...props}>
      {title ? <h2 className="mb-4 text-title font-semibold">{title}</h2> : null}
      {children}
    </section>
  );
}
