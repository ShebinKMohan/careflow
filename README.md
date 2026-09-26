# CareFlow

**Research prototype. Synthetic data only. Not for clinical use.**

CareFlow is a triage check-in kiosk and clinic workflow prototype. A patient checks in privately (silent keypad, ID-tap mock or QR hand-over to their phone), describes their symptoms by typing or speaking in English or Arabic, and an intake graph turns the conversation into structured facts. A plain-code safety rules engine checks those facts for danger signs *before* any model output and alerts the nurse at once; an urgency model then suggests a level, which the nurse always confirms or overrides. The patient gets a token and a room, the doctor gets a briefing in which every fact carries its source, and the visit is written back to a simulated hospital over FHIR R4 and HL7 v2. Everything runs locally on one laptop with a local model by default, and all patient data is synthetic.

## Quick start

Requires macOS (Apple silicon) or Linux with [uv](https://docs.astral.sh/uv/), Node 24+ and pnpm. On a Mac, `scripts/bootstrap_mac.sh` installs the missing tools (no sudo).

```bash
make install && make check
```

- `make install`: `uv sync --locked` and `pnpm install --frozen-lockfile`.
- `make check`: ruff, pyright, pytest, ESLint, tsc, Vitest and the banned-dependency check.
- `uv run python scripts/gen_dev_env.py` creates the gitignored `.env` with random local dev secrets.

## Repository

| Path | What |
|---|---|
| `apps/` | Next.js apps: kiosk, staff (nurse, doctor, queue board, admin), companion phone PWA |
| `services/` | Python: `api`, `intake`, `rules`, `triage`, `voice`, `rag`, `integration` |
| `packages/` | `contracts` (source of truth), `providers` (model provider layer), `ui` (shared UI) |
| `config/`, `infra/`, `eval/`, `scripts/` | Config, Docker infra, evaluation, repo scripts |
| `docs/` | Plan, conventions, agent runbook, decisions (ADRs), evidence |

Design decisions are recorded in [`docs/decisions/`](docs/decisions/). Engineering rules are in [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md).

Research prototype. Synthetic data only. Not for clinical use.
