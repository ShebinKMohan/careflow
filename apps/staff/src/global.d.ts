import type { Locale } from "@careflow/ui/i18n";

import type messages from "../messages/en.json";

// Type-checked message keys and locales for next-intl.
declare module "next-intl" {
  interface AppConfig {
    Locale: Locale;
    Messages: typeof messages;
  }
}
