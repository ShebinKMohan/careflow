# careflow-providers

One async interface for every AI task (chat, streaming, validated structured output), with the provider chosen per task in [`config/providers.yaml`](../../config/providers.yaml).

```python
from careflow_providers import build_provider, load_config, structured, StructuredOk

provider = build_provider("extraction", load_config("config/providers.yaml"))
result = await structured(provider, messages, StructuredFacts)  # StructuredOk | StructuredFailure
```

| Kind | Adapter | Use |
|---|---|---|
| `local` | OpenAI-compatible server (mlx_lm.server on :8081 by default; Ollama, LM Studio) | Default for every live task (Qwen3.5-9B 4-bit MLX, pinned) |
| `api` | LangChain `init_chat_model` (anthropic, openai, gemini, openrouter) | Optional; off unless the owner adds a key; no key raises `ProviderUnavailable` |
| `cli` | `claude -p` / `codex exec` | Offline jobs only (`CAREFLOW_RUNTIME=offline`, else `LivePathForbidden`); never for conversation, extraction or summary |
| replay | `ReplayProvider` wraps any provider | `CAREFLOW_PROVIDER_MODE=record|replay`, `CAREFLOW_CASSETTE=<file>`; the only cassette mechanism in the repo |

`structured()` puts the JSON Schema in the system prompt, strips `<think>` blocks and code fences, validates with Pydantic, retries once with the validation errors, and otherwise returns `StructuredFailure` (the nurse sees "extraction incomplete"; the rules engine still runs). Bad model output never raises.

- `uv run careflow providers show` prints task, provider, model and whether the live path is allowed.
- `scripts/with_llm.sh uv run careflow providers bench --task extraction --n 5` writes `eval/reports/provider-bench-<date>.md`.
- `make llm-local` serves the pinned model in the foreground; `scripts/with_llm.sh <cmd>` starts it only for one command.

Research prototype. Synthetic data only. Not for clinical use.
