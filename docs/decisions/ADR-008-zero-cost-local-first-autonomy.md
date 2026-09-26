# ADR-008: Zero cost, local-first providers and autonomy defaults

## Status

Accepted, 26 Sep 2026 ([M0-01]). Records the owner decisions of 26 Sep 2026 in `docs/CONVENTIONS.md`.

## Context

The whole build runs on one MacBook Pro (M3 Pro, 18 GB RAM, about 67 GB free disk) with no budget, and a coding agent works the backlog unattended. The earlier plan assumed a rented GPU, a larger Qwen model, a paid Arabic reviewer and PhysioNet data.

## Decision

- **Zero cost:** no paid service, API, GPU, cloud GPU (Kaggle/Colab), plan or sign-up. Docker stays at or below 6 GB and one local model is loaded at a time.
- **Local first:** Qwen3.5-9B 4-bit MLX (`mlx-community/Qwen3.5-9B-MLX-4bit`, revision pinned; fallback Qwen3.5-4B) served by `mlx_lm.server` on :8081 is the default for every live task. API providers are pluggable and off unless the owner adds a key; a task with no key raises `ProviderUnavailable` and falls back to the local or replay provider, or is marked `not_run`.
- **Data:** synthetic data plus the public KTAS and COUGHVID datasets only.
- **Arabic:** machine-checked by back-translation only; no Arabic accuracy percentage is published anywhere.
- **Speed targets:** alert p95 <= 1000 ms; voice first-audio p50 <= 1500 ms (cloud) and <= 2500 ms (local). Numbers are published whether or not they meet the targets and are never CI gates.
- **Autonomy:** agents never wait for the owner. They take the documented default, record non-trivial decisions as ADRs, mark missing prerequisites `not_run` with the reason, and never invent a number. The only human stops are in `docs/AGENT_RUNBOOK.md` section 10 (money, publishing, repo settings, irreversible deletion, system settings).
- **Repos:** `ShebinKMohan/careflow` stays private and its settings are never changed; TraceBisect is a read-only pinned dependency.

## Consequences

- Some evaluation rows (hosted-API baselines, cloud voice latency, gated models) are `not_run` unless the owner adds keys or tokens.
- Quality is bounded by a 9B local model; the provider layer keeps an upgrade to a larger or hosted model a one-line config change.
- Every decision an agent takes alone is written down, so the owner can review or reverse it later.
