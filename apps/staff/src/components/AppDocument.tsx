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
      <body className="staff-shell" data-density="desktop">
        <NextIntlClientProvider locale={locale} messages={messages} timeZone={DEFAULT_TIME_ZONE}>
          <a href="#main-content" className="staff-skip-link">{locale === "ar" ? "انتقل إلى المحتوى" : "Skip to content"}</a>
          <SiteHeader />
          <div className="staff-workspace">
            <main id="main-content" className="staff-main">{children}</main>
            <PrototypeFooter locale={locale} />
          </div>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
