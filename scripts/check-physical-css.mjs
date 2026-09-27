#!/usr/bin/env node
// Fail on physical-direction Tailwind classes in the apps (M2-01): Arabic is right-to-left, so
// layout uses logical classes (ms-/me-, ps-/pe-, start-/end-, text-start/end, border-s/e,
// rounded-s/e). Exits 1 and lists every hit.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOTS = ["apps/kiosk/src", "apps/staff/src", "apps/companion/src", "packages/ui/src"];
const BANNED =
  /(?<![\w-])-?(?:ml|mr|pl|pr|left|right|border-l|border-r|rounded-l|rounded-r|rounded-tl|rounded-tr|rounded-bl|rounded-br|scroll-ml|scroll-mr)-(?!to-)[\w.[\]/%-]+|(?<![\w-])text-(?:left|right)(?![\w-])|(?<![\w-])(?:float|clear)-(?:left|right)(?![\w-])/g;

function* files(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) yield* files(path);
    else if (/\.(tsx?|jsx?|css)$/.test(name) && !/\.test\./.test(name)) yield path;
  }
}

const hits = [];
for (const root of ROOTS) {
  for (const path of files(root)) {
    readFileSync(path, "utf8")
      .split("\n")
      .forEach((line, index) => {
        for (const match of line.matchAll(BANNED)) hits.push(`${relative(".", path)}:${index + 1}: ${match[0]}`);
      });
  }
}
if (hits.length) {
  console.error(`check-physical-css: ${hits.length} physical-direction class(es); use logical ones:`);
  for (const hit of hits) console.error(`  ${hit}`);
  process.exit(1);
}
console.log(`check-physical-css: no physical-direction classes in ${ROOTS.join(", ")}`);
