import type { Metadata } from "next";
import { getLocale, getMessages, getTranslations } from "next-intl/server";
import type { ReactNode } from "react";

import { AppDocument } from "@/components/AppDocument";
import { IdleGuard } from "@/components/IdleGuard";
import { RevokedGuard } from "@/components/RevokedGuard";
import { KioskProvider } from "@/lib/session";

import { fontVariables } from "./fonts";
import "./globals.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("meta");
  return { title: t("title"), description: t("description") };
}

export default async function RootLayout({ children }: { children: ReactNode }) {
  const [locale, messages] = await Promise.all([getLocale(), getMessages()]);
  return (
    <AppDocument locale={locale} messages={messages} className={fontVariables}>
      <KioskProvider>
        {children}
        <IdleGuard />
        <RevokedGuard />
      </KioskProvider>
    </AppDocument>
  );
}
