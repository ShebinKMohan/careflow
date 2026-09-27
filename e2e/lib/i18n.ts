/**
 * The apps' own shipped strings (apps/<app>/messages/<locale>.json), so a journey clicks and reads
 * what a patient or nurse sees in either language (M2-01).
 */
import { readFileSync } from "node:fs";

export type Locale = "en" | "ar";
type App = "kiosk" | "staff" | "companion";
type Messages = { [key: string]: string | Messages };

const loaded = new Map<string, Messages>();

function messages(app: App, locale: Locale): Messages {
  const id = `${app}/${locale}`;
  let found = loaded.get(id);
  if (!found) {
    found = JSON.parse(readFileSync(new URL(`../../apps/${app}/messages/${locale}.json`, import.meta.url), "utf8")) as Messages;
    loaded.set(id, found);
  }
  return found;
}

/** The message `key` (dotted) of `app` in `locale` as the screen shows it: plain `{name}`
 * placeholders filled, and the `<ltr>` wrapper (an isolated left-to-right span) dropped. */
export function msg(app: App, locale: Locale, key: string, values: Record<string, string | number> = {}): string {
  const value = key
    .split(".")
    .reduce<string | Messages | undefined>((node, part) => (typeof node === "object" ? node[part] : undefined), messages(app, locale));
  if (typeof value !== "string") throw new Error(`${app} has no ${locale} message "${key}"`);
  return value
    .replace(/\{(\w+)\}/g, (whole, name: string) => (name in values ? String(values[name]) : whole))
    .replace(/<\/?ltr>/g, "");
}

/** A regular expression matching `text` literally. */
export const literally = (text: string) => new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
