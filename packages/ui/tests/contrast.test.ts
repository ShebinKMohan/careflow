// @vitest-environment node
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const tokens = readFileSync(new URL("../src/tokens.css", import.meta.url), "utf8");

function color(name: string): string {
  const match = new RegExp(`--color-${name}:\\s*(#[0-9a-f]{6});`, "i").exec(tokens);
  if (!match?.[1]) throw new Error(`missing --color-${name}`);
  return match[1];
}

function luminance(hex: string): number {
  const channel = (offset: number) => {
    const c = Number.parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

function contrast(a: string, b: string): number {
  const [light, dark] = [luminance(color(a)), luminance(color(b))].sort((x, y) => y - x) as [number, number];
  return (light + 0.05) / (dark + 0.05);
}

// WCAG 2.2: 4.5:1 for text (AA), 3:1 for focus indicators and control borders.
const TEXT_PAIRS: [string, string][] = [
  ["ink", "surface"],
  ["ink", "canvas"],
  ["ink-muted", "surface"],
  ["ink-muted", "canvas"],
  ["on-primary", "primary"],
  ["on-secondary", "secondary"],
  ["on-danger", "danger"],
  ["on-success", "success"],
  ["on-footer", "footer"],
  ["primary", "surface"],
];
const NON_TEXT_PAIRS: [string, string][] = [
  ["focus", "surface"],
  ["focus", "canvas"],
  ["border", "surface"],
  ["border", "canvas"],
];

describe("token contrast (WCAG 2.2 AA)", () => {
  it.each(TEXT_PAIRS)("text %s on %s is at least 4.5:1", (fg, bg) => {
    expect(contrast(fg, bg)).toBeGreaterThanOrEqual(4.5);
  });

  it.each(NON_TEXT_PAIRS)("%s against %s is at least 3:1", (fg, bg) => {
    expect(contrast(fg, bg)).toBeGreaterThanOrEqual(3);
  });
});
