import { DEFAULT_TIME_ZONE, LOCALE_COOKIE, resolveLocale } from "@careflow/ui/i18n";
import { cookies } from "next/headers";
import { getRequestConfig } from "next-intl/server";

import { MESSAGES } from "./messages";

// No locale routing: every request reads the NEXT_LOCALE cookie that LanguageToggle sets.
export default getRequestConfig(async () => {
  const locale = resolveLocale((await cookies()).get(LOCALE_COOKIE)?.value);
  return { locale, messages: MESSAGES[locale], timeZone: DEFAULT_TIME_ZONE };
});
