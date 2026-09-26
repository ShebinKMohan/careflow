import { Noto_Sans, Noto_Sans_Arabic } from "next/font/google";

const latin = Noto_Sans({ subsets: ["latin"], variable: "--font-latin", display: "swap" });
const arabic = Noto_Sans_Arabic({ subsets: ["arabic"], variable: "--font-arabic", display: "swap" });

/** CSS variables consumed by @careflow/ui/tokens.css (--font-sans). */
export const fontVariables = `${latin.variable} ${arabic.variable}`;
