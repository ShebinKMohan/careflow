# ADR-004: Subscription CLIs for offline jobs only

## Status

Accepted, 26 Sep 2026 ([M0-01]).

## Context

The owner has subscription CLIs (Claude Code `claude -p`, Codex `codex exec`) that can generate synthetic test data, label data and judge evaluation runs at no marginal cost. They take seconds to start, are not built to serve other people, and consumer subscriptions may not permit powering a service for others. A test call of `claude -p` reported a per-token cost, so its billing is uncertain.

## Decision

- The provider layer has three kinds: local (Qwen3.5-9B MLX, the default), API (optional, off without a key) and subscription CLI.
- CLIs serve **offline tasks only**: `test_data` and `eval_judge` (and optionally the offline `translation_check`). They are never used for a live patient turn and never run in CI.
- At most 200 CLI calls per issue. Agents prefer writing synthetic data themselves in-session; the default CLI is `codex exec`.
- The eval judge is never the same model as the generator it judges.

## Consequences

- Live latency never depends on a CLI start-up.
- Offline datasets record which tool produced and which checked them (`reviewed_by: "agent:<tool>/<model>"`, `review_method: automated`).
- If a CLI is unavailable, the offline job falls back to the local model or is marked `not_run` with the reason.
