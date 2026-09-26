# ADR-001: One monorepo with a uv and a pnpm workspace

## Status

Accepted, 26 Sep 2026 ([M0-01]).

## Context

CareFlow spans a kiosk, staff and phone apps (TypeScript), an API, an intake graph, a rules engine, an urgency model, voice, RAG and hospital integration (Python), and shared contracts. Several coding agents build these in parallel lanes, and every lane builds against the same contracts. Separate repositories would need versioned releases of the contracts before any lane could use a change, and cross-cutting changes (a new fact field used by the rules, the API and the nurse screen) would span several pull requests.

## Decision

- One git repository, `ShebinKMohan/careflow` (private), with the layout in `docs/CONVENTIONS.md`: `apps/`, `services/`, `packages/`, `config/`, `infra/`, `eval/`, `e2e/`, `scripts/`, `docs/`.
- Python: one **uv workspace** with a single `uv.lock` (hashes). Each member is `services/<name>` or `packages/<name>` with `src/careflow_<name>/` and its own `tests/`. Members are added explicitly to `[tool.uv.workspace] members`.
- TypeScript: one **pnpm workspace** with a single `pnpm-lock.yaml`; shared tool versions live in the pnpm catalog in `pnpm-workspace.yaml`.
- One root `Makefile` is the entry point for people, agents and CI: `make install && make check`.

## Consequences

- A contract change, its generated types and every consumer can land in one pull request and one CI run.
- Both lockfiles pin every transitive dependency; `uv sync --locked` and `pnpm install --frozen-lockfile` fail on drift.
- Heavy or platform-specific dependencies (torch, MLX, speech models) must stay in the members that need them so `services/api` stays light and CI stays on Ubuntu.
- CI runs per language, so a docs-only change skips it (`paths-ignore`).
