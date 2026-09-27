import { IBM_Plex_Sans_Arabic, Noto_Sans } from "next/font/google";

const latin = Noto_Sans({ subsets: ["latin"], variable: "--font-latin", display: "swap" });
const arabic = IBM_Plex_Sans_Arabic({
  subsets: ["arabic"],
  weight: ["400", "500", "600"],
  variable: "--font-arabic",
  display: "swap",
});

/** CSS variables consumed by @careflow/ui/tokens.css (--font-sans). */
export const fontVariables = `${latin.variable} ${arabic.variable}`;
