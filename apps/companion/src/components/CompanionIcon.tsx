import type { CSSProperties } from "react";

type IconName = "voice" | "phone" | "check" | "clock" | "message" | "arrow" | "shield" | "alert" | "plus" | "heart" | "family" | "bone" | "skin" | "medical";
const paths: Record<IconName, string> = {
  voice: "M9 5a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0V5Zm-3 6v1a6 6 0 0 0 12 0v-1M12 18v4m-4 0h8",
  heart: "M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z",
  family: "M15 6a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM6 13l6-2 6 2m-6-2v6m0 0-4 5m4-5 4 5",
  bone: "M5 3a3 3 0 0 0-1 5l1 1 10 10a3 3 0 1 0 5-1 3 3 0 0 0-1-5L9 3A3 3 0 0 0 5 3Z",
  skin: "M12 3C9 7 5 11 5 15a7 7 0 0 0 14 0c0-4-4-8-7-12Zm-4 12a4 4 0 0 0 4 4",
  medical: "M6 3v6a6 6 0 0 0 12 0V3M4 3h4m8 0h4M12 15v2a4 4 0 0 0 8 0v-2m2-2a2 2 0 1 1-4 0 2 2 0 0 1 4 0Z",
  phone: "M8 3h8a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Zm2 14h4M10 6h4",
  check: "m5 12 4 4L19 6",
  clock: "M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
  message: "M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2v-10.5a9.5 9.5 0 0 1 19 0ZM7 10h9M7 14h6",
  arrow: "M5 12h14m-5-5 5 5-5 5",
  shield: "m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Zm-4 9 3 3 5-6",
  alert: "M12 8v5m0 3v.01M10.3 3.9 2.2 18a2 2 0 0 0 1.7 3h16.2a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z",
  plus: "M12 5v14M5 12h14",
};

export function CompanionIcon({ name, className, style }: { name: IconName; className?: string; style?: CSSProperties }) {
  return <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className} style={style}><path d={paths[name]} /></svg>;
}
