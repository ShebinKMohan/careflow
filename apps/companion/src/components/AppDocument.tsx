import { PrototypeFooter } from "@careflow/ui";
import { DEFAULT_TIME_ZONE, direction, type Locale } from "@careflow/ui/i18n";
import { type AbstractIntlMessages, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";

import { ServiceWorkerRegistrar } from "./ServiceWorkerRegistrar";
import { SiteHeader } from "./SiteHeader";
import { TokenRenewer } from "./TokenRenewer";

export interface AppDocumentProps {
  locale: Locale;
  messages: AbstractIntlMessages;
  className?: string;
  children: ReactNode;
}

/**
 * The <html> shell: lang and dir follow the locale, every page gets the prototype footer, the
 * service worker (static shell only) is registered, and the phone's session token is kept alive.
 */
export function AppDocument({ locale, messages, className, children }: AppDocumentProps) {
  return (
    <html lang={locale} dir={direction(locale)} data-density="phone" className={className}>
      <body className="companion-body">
        <NextIntlClientProvider locale={locale} messages={messages} timeZone={DEFAULT_TIME_ZONE}>
          <SiteHeader />
          <main id="main-content" className="companion-main">{children}</main>
          <PrototypeFooter locale={locale} />
          <ServiceWorkerRegistrar />
          <TokenRenewer />
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
