import type { CSSProperties } from "react";

export type KioskIconName = "cross" | "arrow" | "shield" | "camera" | "voice" | "record" | "person" | "child" | "check" | "help" | "card" | "phone" | "heart" | "skin" | "bone" | "ear" | "eye" | "women" | "lab" | "doctor" | "calendar" | "edit" | "back" | "tooth";

const paths: Record<KioskIconName, string> = {
  cross: "M9 3h6v6h6v6h-6v6H9v-6H3V9h6z",
  arrow: "M5 12h14m-6-6 6 6-6 6",
  shield: "m12 3 8 3v5c0 5-4 8-8 10-4-2-8-5-8-10V6l8-3Zm-4 9 3 3 5-6",
  camera: "M4 7h4l2-3h4l2 3h4v13H4V7Zm11 6a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
  voice: "M9 5a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0V5Zm-3 6v1a6 6 0 0 0 12 0v-1m-6 7v4m-4 0h8",
  record: "M7 3h10v3h3v15H4V6h3m0-3v5h10V3H7Zm1 10h8m-8 4h5",
  person: "M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM4 21v-2a8 8 0 0 1 16 0v2",
  child: "M15 7a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM5 14l7-2 7 2m-7-2v5m0 0-4 5m4-5 4 5",
  check: "m5 12 4 4L19 6",
  help: "M9 9a3 3 0 1 1 5 2c-2 1-2 2-2 3m0 3v.1M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z",
  card: "M3 5h18v14H3V5Zm3 4h4v4H6V9Zm8 1h4m-4 4h4M6 16h5",
  phone: "M7 2h10a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1Zm3 3h4m-3 14h2",
  heart: "M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8ZM3 12h4l2-4 4 8 2-4h6",
  skin: "M12 2C9 7 5 10 5 15a7 7 0 0 0 14 0c0-5-4-8-7-13Zm-3 12h.01M12 17h.01m3-4h.01",
  bone: "m8 5 11 11a3 3 0 1 1-3 5 3 3 0 0 1-4-3L3 9A3 3 0 1 1 5 3a3 3 0 0 1 3 2Z",
  ear: "M6 9a6 6 0 0 1 12 0c0 4-4 4-4 8a4 4 0 0 1-8 0m4-8a2 2 0 0 1 4 0c0 2-2 2-2 4",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Zm13 0a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
  women: "M17 8a5 5 0 1 1-10 0 5 5 0 0 1 10 0Zm-5 5v9m-4-4h8",
  lab: "M8 2h8m-6 0v7L3 20a1 1 0 0 0 1 2h16a1 1 0 0 0 1-2L14 9V2M7 15h10m-7 3h.01m4 1h.01",
  doctor: "M8 3v6a4 4 0 0 0 8 0V3M6 3h4m4 0h4m-6 10v3a5 5 0 0 0 10 0v-2m0-2a2 2 0 1 1-4 0 2 2 0 0 1 4 0Z",
  calendar: "M4 5h16v16H4V5Zm4-3v6m8-6v6M4 11h16m-12 4h2m4 0h2m-8 3h2",
  edit: "m16 3 5 5-12 12-6 1 1-6L16 3Zm-3 3 5 5",
  back: "M19 12H5m6-6-6 6 6 6",
  tooth: "M12 4C8 1 3 3 4 9l2 10c1 4 3 2 4-2l2-3 2 3c1 4 3 6 4 2l2-10c1-6-4-8-8-5Z",
};

export function KioskIcon({ name, className, style }: { name: KioskIconName; className?: string; style?: CSSProperties }) {
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className={className} style={style}><path d={paths[name]} /></svg>;
}
