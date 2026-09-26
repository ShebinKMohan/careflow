// @vitest-environment node
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

// Right-to-left support: only logical utilities (ms-, me-, ps-, pe-, start-, end-, text-start ...).
const ROOT = fileURLToPath(new URL("../../..", import.meta.url));
const SOURCES = ["packages/ui/src", "apps/kiosk/src", "apps/staff/src", "apps/companion/src"];
const PHYSICAL =
  /(?<![\w-])-?(?:ml|mr|pl|pr|left|right|border-l|border-r|rounded-l|rounded-r|rounded-tl|rounded-tr|rounded-bl|rounded-br|scroll-ml|scroll-mr|scroll-pl|scroll-pr)-[\w./[\]-]+|(?<![\w-])(?:text-left|text-right|float-left|float-right|clear-left|clear-right)(?![\w-])/g;

/** Drop comments so prose such as "left to right" in a comment is not flagged. */
function code(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
}

function files(dir: string): string[] {
  let entries: string[];
  try {
    entries = readdirSync(dir);
  } catch {
    return [];
  }
  return entries.flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return files(path);
    return /\.(tsx?|css)$/.test(name) ? [path] : [];
  });
}

describe("logical utilities only", () => {
  it("uses no physical left/right utility classes", () => {
    const scanned = SOURCES.flatMap((source) => files(join(ROOT, source)));
    expect(scanned.length).toBeGreaterThan(20); // the guard must really see the sources
    const offenders = scanned.flatMap((path) => {
      const found = code(readFileSync(path, "utf8")).match(PHYSICAL) ?? [];
      return found.map((cls) => `${relative(ROOT, path)}: ${cls}`);
    });
    expect(offenders).toEqual([]);
  });
});
