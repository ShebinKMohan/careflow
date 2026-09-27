import { PrototypeFooter } from "@careflow/ui";
import { DEFAULT_TIME_ZONE, direction, type Locale } from "@careflow/ui/i18n";
import { type AbstractIntlMessages, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";

import { SiteHeader } from "./SiteHeader";

export interface AppDocumentProps {
  locale: Locale;
  messages: AbstractIntlMessages;
  className?: string;
  children: ReactNode;
}

/** The <html> shell: lang and dir follow the locale, and every page gets the prototype footer. */
export function AppDocument({ locale, messages, className, children }: AppDocumentProps) {
  return (
    <html lang={locale} dir={direction(locale)} className={className}>
      <body className="flex min-h-dvh flex-col">
        <NextIntlClientProvider locale={locale} messages={messages} timeZone={DEFAULT_TIME_ZONE}>
          <SiteHeader />
          <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-gutter py-8">{children}</main>
          <PrototypeFooter locale={locale} />
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
