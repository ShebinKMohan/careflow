import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { KEYPAD_LENGTH, Keypad } from "@/components/Keypad";

const LABELS = { display: "Keypad ID", backspace: "Delete the last digit", clear: "Clear", submit: "Check in" };

function setup() {
  const onSubmit = vi.fn();
  render(<Keypad labels={LABELS} onSubmit={onSubmit} />);
  const display = screen.getByLabelText("Keypad ID") as HTMLInputElement;
  const press = async (digits: string) => {
    for (const digit of digits) await userEvent.click(screen.getByRole("button", { name: digit }));
  };
  return { onSubmit, display, press };
}

describe("kiosk keypad", () => {
  it("shows only dots, never the digits, and opens no on-screen keyboard", async () => {
    const { display, press } = setup();
    await press("1000");
    expect(display.value).toBe("••••");
    expect(display).not.toHaveValue(expect.stringMatching(/\d/));
    expect(display).toHaveAttribute("inputmode", "none");
    expect(display).toHaveAttribute("readonly");
  });

  it("stops at 6 digits and submits them only when complete", async () => {
    const { display, press, onSubmit } = setup();
    const submit = screen.getByRole("button", { name: "Check in" });
    await press("10000");
    expect(submit).toBeDisabled();
    await press("12");  // the 7th digit is ignored
    expect(display.value).toBe("•".repeat(KEYPAD_LENGTH));
    await userEvent.click(submit);
    expect(onSubmit).toHaveBeenCalledWith("100001");
  });

  it("deletes the last digit and clears the entry", async () => {
    const { display, press, onSubmit } = setup();
    await press("123");
    await userEvent.click(screen.getByRole("button", { name: "Delete the last digit" }));
    expect(display.value).toBe("••");
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(display.value).toBe("");
    await press("100005");
    await userEvent.click(screen.getByRole("button", { name: "Check in" }));
    expect(onSubmit).toHaveBeenCalledWith("100005");
  });
});
