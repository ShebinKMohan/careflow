import { PROTOTYPE_FOOTER, PROTOTYPE_FOOTER_AR } from "@careflow/ui";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AbstractIntlMessages } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppDocument } from "@/components/AppDocument";
import { MESSAGES } from "@/i18n/messages";
import requestConfig from "@/i18n/request";

// next/headers reads the browser cookie jar, as the server would read the request's cookies.
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => {
      const match = document.cookie.split("; ").find((part) => part.startsWith(`${name}=`));
      return match ? { name, value: match.slice(name.length + 1) } : undefined;
    },
  }),
}));
vi.mock("next-intl/server", () => ({ getRequestConfig: <T,>(factory: T) => factory }));
const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh, push: vi.fn(), replace: vi.fn() }) }));

async function renderDocument() {
  const config = await requestConfig({ requestLocale: Promise.resolve(undefined) });
  cleanup();
  render(
    <AppDocument locale={config.locale ?? "en"} messages={config.messages as AbstractIntlMessages}>
      <p>page</p>
    </AppDocument>,
    { container: document },
  );
}

function keys(value: unknown, prefix = ""): string[] {
  if (typeof value !== "object" || value === null) return [prefix];
  return Object.entries(value).flatMap(([key, child]) => keys(child, prefix ? `${prefix}.${key}` : key));
}

describe("staff root layout", () => {
  beforeEach(() => {
    document.cookie = "NEXT_LOCALE=; path=/; max-age=0";
  });
  afterEach(() => cleanup());

  it("renders the footer, and choosing Arabic sets <html dir=rtl>", async () => {
    await renderDocument();
    expect(document.documentElement).toHaveAttribute("dir", "ltr");
    expect(document.documentElement).toHaveAttribute("lang", "en");
    expect(screen.getByRole("contentinfo")).toHaveTextContent(PROTOTYPE_FOOTER);

    await userEvent.click(screen.getByRole("button", { name: "العربية" }));
    expect(refresh).toHaveBeenCalled();
    await renderDocument(); // the refresh re-renders the layout with the new cookie

    expect(document.documentElement).toHaveAttribute("dir", "rtl");
    expect(document.documentElement).toHaveAttribute("lang", "ar");
    expect(screen.getByRole("contentinfo")).toHaveTextContent(PROTOTYPE_FOOTER_AR);
  });

  it("has an Arabic message for every English key", () => {
    expect(keys(MESSAGES.ar).sort()).toEqual(keys(MESSAGES.en).sort());
  });
});
