// `pnpm --filter kiosk models:sync` (M3-02): the kiosk's model files, served by the kiosk itself.
//
// - The registry's current Laya model (services/triage/registry/registry.json) goes to
//   public/models/laya/<version>/. Every file is checked against the registry's sha256 while it is
//   copied, and public/models/manifest.json lists the sha256s the worker verifies before loading.
// - onnxruntime-web's WASM runtime (dist/*.wasm and *.mjs) goes to public/ort/: no CDN.
//
// Both folders are git-ignored (the int8 encoder is about 500 MB). Research prototype.
import { createHash } from "node:crypto";
import { createReadStream, createWriteStream } from "node:fs";
import { copyFile, mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { Transform } from "node:stream";
import { pipeline } from "node:stream/promises";
import { fileURLToPath } from "node:url";

const SHIPPED = ["encoder.onnx", "head.onnx", "tokenizer.json", "rl_agent_config.json", "question.json"];

const kiosk = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repo = path.resolve(kiosk, "../..");
const registry = JSON.parse(await readFile(path.join(repo, "services/triage/registry/registry.json"), "utf8"));
const version = registry.current?.laya;
const entry = registry.entries.find((e) => e.family === "laya" && e.version === version);
if (!entry) {
  console.error("models:sync: the registry has no current laya model");
  process.exit(1);
}

async function copyVerified(source, target, expected) {
  const digest = createHash("sha256");
  const tap = new Transform({
    transform(chunk, _encoding, done) {
      digest.update(chunk);
      done(null, chunk);
    },
  });
  await pipeline(createReadStream(source), tap, createWriteStream(target));
  const actual = digest.digest("hex");
  if (actual !== expected) {
    await rm(target, { force: true });
    throw new Error(`models:sync: ${path.basename(source)} does not match the registry`);
  }
  return actual;
}

const models = path.join(kiosk, "public", "models");
const target = path.join(models, "laya", version);
await rm(models, { recursive: true, force: true });
await mkdir(target, { recursive: true });
const files = {};
for (const name of SHIPPED) {
  const listed = entry.files.find((file) => file.path === name);
  if (!listed) throw new Error(`models:sync: ${name} is not in the registry entry for laya ${version}`);
  files[name] = await copyVerified(path.join(repo, entry.artifact, name), path.join(target, name), listed.sha256);
}
const manifest = { laya: { version, path: `/models/laya/${version}`, sha256: entry.sha256, files } };
await writeFile(path.join(models, "manifest.json"), `${JSON.stringify(manifest, null, 1)}\n`);

const ortDist = path.join(kiosk, "node_modules", "onnxruntime-web", "dist");
const ortTarget = path.join(kiosk, "public", "ort");
await rm(ortTarget, { recursive: true, force: true });
await mkdir(ortTarget, { recursive: true });
const runtime = (await readdir(ortDist)).filter((name) => name.endsWith(".wasm") || name.endsWith(".mjs"));
for (const name of runtime) await copyFile(path.join(ortDist, name), path.join(ortTarget, name));

console.log(`models:sync: laya ${version} (${SHIPPED.length} files verified) and ${runtime.length} onnxruntime-web files`);
