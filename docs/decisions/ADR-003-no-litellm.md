# ADR-003: No LiteLLM

## Status

Accepted, 26 Sep 2026 ([M0-01]).

## Context

LiteLLM is a popular multi-provider LLM client. Its PyPI package was compromised in March 2026. CareFlow handles (synthetic) patient conversations and must be able to show a clean supply chain. The provider layer needs chat, structured output and streaming across a local OpenAI-compatible server, a few hosted APIs and two subscription CLIs, which does not need a large dependency.

## Decision

- LiteLLM is banned in every form: as a Python dependency, an npm package or a transitive lock entry.
- `scripts/check_banned_deps.py` fails the build if `litellm` appears in any `pyproject.toml`, `uv.lock`, `package.json` or `pnpm-lock.yaml`, and if any Python dependency has no version constraint. It runs in `make check` and in the CI `security` job, with pytest coverage.
- The provider layer (`packages/providers`, [M0-06]) reuses the provider router from the owner's plivo-voice-agent, LangChain's `init_chat_model` for API providers, and one small adapter for the CLIs.

## Consequences

- One more small adapter to maintain instead of a large dependency.
- A contributor or agent who adds LiteLLM, directly or through a transitive dependency, gets a red build with the reason.
- The check must run before anything installs dependencies: a plain `uv run` first re-resolves and installs a newly declared dependency (found while verifying [M0-01]). So `make install` and `make check` run it first with `uv run --no-project`, the CI `security` job runs it without syncing, and the Makefile exports `UV_LOCKED=1` so `uv run` fails fast on an unlocked change instead of installing it.
- The ban is recorded here so a later reviewer knows it is deliberate, not an oversight.
