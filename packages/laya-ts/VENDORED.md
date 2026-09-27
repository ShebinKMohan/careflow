# Vendored: laya-ts

- **Source:** https://github.com/NandhaKishorM/laya, folder `laya-ts/`, at commit `4066d5d5fbf08b66c6757ddeedbd797bd7655bc0`.
- **Why vendored:** it is not on npm.
- **Licence:** Apache-2.0; `LICENSE` is the upstream root licence.
- **Used by:** the kiosk's Web Worker, to run the fine-tuned Laya model on onnxruntime-web (WASM, int8) (M3-02, ADR-043).

## What is here

- `src/`, `scripts/export_onnx.py`, `tests/`, `README.md` and `tsconfig.json`, as upstream, except for the patches below.
- **Not vendored:** `examples/` and `package-lock.json`.

## Local changes

1. **`package.json` replaced.**
   - It is named `@careflow/laya-ts`, marked private, and exports the TypeScript source, which the kiosk compiles through Next's `transpilePackages`; there is no build step.
   - `onnxruntime-web` is pinned to 1.30.0.
   - `onnxruntime-node` (for the parity test) arrives with that test in the test phase.
   - The upstream tests run as `test:upstream`, not `test`, so `pnpm -r test` does not run them. They were written for vitest 2 and some fetch model files.
2. **Patch (a): `executionProviders`.** `ProviderOptions` and `Agent.load` take `executionProviders`, and `createWebProvider` passes it to both the encoder and head sessions. The kiosk asks for `["wasm"]`: WebGPU is not used for the int8 model.
3. **Patch (b): a literal import.** `createWebProvider` imports `onnxruntime-web` with a literal `import("onnxruntime-web")`. The computed upstream `import("onnxruntime-" + "web")` cannot be resolved by the bundler, and a browser has no bare-module resolution.
4. **Patch (c): Web Worker detection.** `Agent.load` treats a Web Worker (`WorkerGlobalScope`) as a browser. Upstream tests only for `window`, which a worker lacks, so it took the Node path.

Every patch is marked `CareFlow patch` in the source. To update: copy the new upstream folder, re-apply the patches and update the commit above.
