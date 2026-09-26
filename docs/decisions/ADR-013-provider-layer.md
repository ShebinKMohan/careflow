# ADR-013: Model provider layer

## Status

Accepted, 26 Sep 2026 ([M0-06]).

## Context

Every AI task (conversation, extraction, summary, eval judge, test data, translation check) must be switchable between a local model, a hosted API and a subscription CLI with one config line, and every output must be validated before the rules engine or a nurse sees it. The build is zero-cost and local-first (ADR-008), CLIs are offline-only (ADR-004) and LiteLLM is banned (ADR-003).

## Decision

- **One protocol, four adapters.** `ChatProvider` (`chat`, `stream`) is implemented by `LocalProvider` (OpenAI-compatible HTTP), `ApiProvider` (LangChain `init_chat_model`; `with_structured_output` is available as `structured_native`), `CliProvider` (Claude Code / Codex, argument list, 300 s timeout, `LivePathForbidden` unless `CAREFLOW_RUNTIME=offline`) and `ReplayProvider` (record/replay cassettes keyed by the SHA-256 of the canonical task, model, messages and temperature). `build_provider` is a single `match` on the configured kind.
- **Local model: `mlx-community/Qwen3.5-9B-MLX-4bit` at revision `938d8919941c6e7efd3c7150eff7fe9d12afa631`**, served by mlx-lm 0.31.3 (which has a `qwen3_5` model). It loads and returns a valid `StructuredFacts` on the first attempt; no fallback was needed. `mlx-community/Qwen3.5-4B-4bit` at `0e7ffd5c629ef7719d4cbc04069232580bfa9d9c` is the documented fallback and was benched too.
- **Thinking is switched off per request** with `chat_template_kwargs: {enable_thinking: false}` (Qwen's template honours it and mlx_lm.server passes it through); `<think>` stripping stays as a backstop.
- **`served_model` defaults to `default_model`**: mlx_lm.server treats any other model name in a request as a model to load, so the logical name in `providers.yaml` (`qwen3.5-9b-4bit`) is used for reports and cassette keys only.
- **One system message**: `structured()` merges its schema instruction into the caller's system message, because Qwen's template rejects a second system message.
- **openai SDK pinned to 2.x (2.54)**: openai 3.x moved to the `httpx2` client, which respx cannot intercept; the issue requires respx-based offline tests. langchain-openai accepts `openai>=2.45,<4`. Revisit when respx supports httpx2.
- **Codex** is read through `--output-last-message <file>` (codex-cli 0.154), with JSONL parsing only as a fallback, so no undocumented event names are relied on.
- **Transport retry** (ported from the owner's plivo-voice-agent): 429, 5xx and connection errors only, capped exponential backoff honouring `Retry-After`, never after streaming has started; the SDK's own retries are off.

## Consequences

- Switching `extraction` to an API model is one line in `providers.yaml`; without a key the task raises `ProviderUnavailable` and callers fall back or record `not_run`.
- The live structured check depends on prompt quality: the smoke prompt has to name the fields to fill, and span offsets from the model are approximate. M1-06 owns the real extraction prompt and recomputes spans from the quotes.
- mlx-lm is a macOS-arm64-only dev dependency; CI (Ubuntu) never loads a model and uses fakes and replay.
