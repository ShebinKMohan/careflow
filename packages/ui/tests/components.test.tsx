import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  BigButton,
  Card,
  direction,
  Keypad,
  LanguageToggle,
  PROTOTYPE_FOOTER_AR,
  PrototypeFooter,
  readLocaleCookie,
  resolveLocale,
} from "../src/index";

const LABELS = { display: "Keypad ID", clear: "Clear", submit: "Check in" };

function clearCookie() {
  document.cookie = "NEXT_LOCALE=; path=/; max-age=0";
}

describe("Keypad", () => {
  it("collects six digits, then submits them", async () => {
    const onSubmit = vi.fn();
    render(<Keypad labels={LABELS} onSubmit={onSubmit} />);
    const submit = screen.getByRole("button", { name: "Check in" });
    expect(submit).toBeDisabled();
    for (const digit of "100001") await userEvent.click(screen.getByRole("button", { name: digit }));
    expect(screen.getByLabelText("Keypad ID")).toHaveTextContent("100001");
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    expect(onSubmit).toHaveBeenCalledWith("100001");
  });

  it("ignores extra digits and clears", async () => {
    render(<Keypad labels={LABELS} onSubmit={() => undefined} />);
    for (const digit of "12345678") await userEvent.click(screen.getByRole("button", { name: digit }));
    expect(screen.getByLabelText("Keypad ID")).toHaveTextContent("123456");
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.getByLabelText("Keypad ID")).toHaveTextContent("······");
  });

  it("keeps the phone-keypad layout left to right, also in Arabic", () => {
    const { container } = render(<Keypad labels={LABELS} onSubmit={() => undefined} />);
    expect(container.firstElementChild).toHaveAttribute("dir", "ltr");
  });
});

describe("touch targets", () => {
  it("gives buttons and keys the 64 px minimum", () => {
    render(
      <>
        <BigButton>Start</BigButton>
        <Keypad labels={LABELS} onSubmit={() => undefined} />
        <LanguageToggle locale="en" />
      </>,
    );
    for (const button of screen.getAllByRole("button")) {
      expect(button.className).toContain("min-h-touch");
      expect(button.className).toContain("min-w-touch");
    }
  });
});

describe("LanguageToggle", () => {
  beforeEach(clearCookie);

  it("sets the NEXT_LOCALE cookie and reports the choice", async () => {
    const onChange = vi.fn();
    render(<LanguageToggle locale="en" onChange={onChange} />);
    expect(screen.getByRole("button", { name: "English" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "العربية" }));
    expect(readLocaleCookie()).toBe("ar");
    expect(onChange).toHaveBeenCalledWith("ar");
  });
});

describe("locale helpers", () => {
  it("falls back to English and maps Arabic to rtl", () => {
    expect(resolveLocale(undefined)).toBe("en");
    expect(resolveLocale("fr")).toBe("en");
    expect(resolveLocale("ar")).toBe("ar");
    expect(direction("ar")).toBe("rtl");
    expect(direction("en")).toBe("ltr");
    expect(readLocaleCookie("a=1; NEXT_LOCALE=ar")).toBe("ar");
  });
});

describe("PrototypeFooter and Card", () => {
  it("renders the disclaimer in each language", () => {
    render(<PrototypeFooter locale="ar" />);
    expect(screen.getByRole("contentinfo")).toHaveTextContent(PROTOTYPE_FOOTER_AR);
  });

  it("renders a titled card", () => {
    render(<Card title="Status">body</Card>);
    expect(screen.getByRole("heading", { name: "Status" })).toBeInTheDocument();
  });
});
