import type { Metadata, Viewport } from "next";
import { getLocale, getMessages, getTranslations } from "next-intl/server";
import type { ReactNode } from "react";

import { AppDocument } from "@/components/AppDocument";

import { fontVariables } from "./fonts";
import "./globals.css";

export const viewport: Viewport = { themeColor: "#0a4f94", width: "device-width", initialScale: 1 };

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("meta");
  return { title: t("title"), description: t("description") };
}

export default async function RootLayout({ children }: { children: ReactNode }) {
  const [locale, messages] = await Promise.all([getLocale(), getMessages()]);
  return (
    <AppDocument locale={locale} messages={messages} className={fontVariables}>
      {children}
    </AppDocument>
  );
}
