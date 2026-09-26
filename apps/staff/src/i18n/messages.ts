import type { Locale } from "@careflow/ui/i18n";

import ar from "../../messages/ar.json";
import en from "../../messages/en.json";

export type Messages = typeof en;

/** Arabic must have every English key (checked here by the type and in tests/layout.test.tsx). */
export const MESSAGES: Record<Locale, Messages> = { en, ar };
