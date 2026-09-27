# CareFlow developer entry points. Research prototype. Synthetic data only. Not for clinical use.
# `make install && make check` is the whole local gate; CI runs the same targets per language.

.DEFAULT_GOAL := help
SHELL := /bin/bash
# `uv run` must never re-resolve: a dependency added to a pyproject.toml without `uv lock` fails
# fast instead of being downloaded and installed before the banned-dependency check can run.
export UV_LOCKED := 1

.PHONY: help install install-py install-node lint lint-py lint-node typecheck typecheck-py \
	typecheck-node test test-py test-node banned-deps check contracts contracts-lint \
	contracts-drift contracts-breaking contracts-check up down seed ps logs nuke \
	llm-local api migrate seed-dev mock e2e voice-models voice

help: ## List the targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-16s %s\n", $$1, $$2}'

install: install-py install-node ## Install locked Python and Node dependencies

install-py: banned-deps
	uv sync --locked --all-packages

install-node:
	pnpm install --frozen-lockfile

lint: lint-py lint-node ## Ruff (lint + format check) and ESLint

lint-py:
	uv run ruff check .
	uv run ruff format --check .

lint-node:
	pnpm -r lint

typecheck: typecheck-py typecheck-node ## Pyright and tsc

typecheck-py:
	uv run pyright

typecheck-node:
	pnpm -r typecheck

test: test-py test-node ## Pytest and Vitest

test-py:
	uv run pytest

test-node:
	pnpm -r test

# Runs before any sync, outside the project environment, so a banned package is never installed.
banned-deps: ## Fail on LiteLLM or any unconstrained Python dependency
	env -u UV_LOCKED uv run --no-project --python 3.12 python scripts/check_banned_deps.py

check: banned-deps lint typecheck test ## Everything CI checks, except the secret and CVE scans

CONTRACTS := packages/contracts

# Regenerate typed models from packages/contracts. Deterministic: CI checks `git diff --exit-code`.
contracts: ## Regenerate Python models, TS types and alert templates from packages/contracts
	$(CONTRACTS)/codegen/generate.sh

contracts-lint: ## Redocly lint of openapi.yaml (0 errors required)
	pnpm --filter @careflow/contracts exec redocly lint openapi.yaml

contracts-drift: contracts ## Regenerate, then fail if the committed outputs differ
	git diff --exit-code -- $(CONTRACTS)
	test -z "$$(git status --porcelain --untracked-files=all -- $(CONTRACTS))"

contracts-breaking: ## oasdiff breaking changes against origin/main (skipped when main has no spec)
	scripts/contracts_breaking.sh

contracts-check: contracts-lint contracts-drift contracts-breaking ## All contract gates CI runs

# --- Local infrastructure (M0-05): Postgres 17, Valkey, HAPI FHIR -------------------------------
COMPOSE := docker compose --env-file .env -f infra/docker-compose.yml
SEED_SYNTHEA ?= 1

up: ## Start Postgres, Valkey and HAPI FHIR and wait until they are healthy
	scripts/ensure_docker.sh
	python3 scripts/gen_dev_env.py
	python3 scripts/check_mem_budget.py
	$(COMPOSE) up -d --build --wait postgres valkey
	$(COMPOSE) up -d hapi
	python3 scripts/wait_for_http.py http://localhost:8080/fhir/metadata 180

down: ## Stop the services; named volumes keep the data
	$(COMPOSE) down

seed: ## Load Synthea patients (SEED_SYNTHEA=0 skips them) and the 7 personas; FORCE=1 reloads
	@if [ "$(SEED_SYNTHEA)" != "0" ]; then infra/seed/run_synthea.sh; fi
	uv run python infra/seed/load_fhir.py $(if $(filter 0,$(SEED_SYNTHEA)),--personas-only,)

ps: ## Service status
	$(COMPOSE) ps

logs: ## Last 200 log lines of every service
	$(COMPOSE) logs --tail 200

nuke: ## Delete every local data volume (asks first). Agents never run this: use make seed FORCE=1
	@read -r -p "This deletes all CareFlow Docker volumes (Postgres, Valkey, HAPI data). Type nuke: " a; \
		[ "$$a" = "nuke" ] || { echo "aborted"; exit 1; }
	$(COMPOSE) down -v

# --- Local LLM (M0-06) ------------------------------------------------------------------------
LLM_MODEL := mlx-community/Qwen3.5-9B-MLX-4bit
LLM_REVISION := 938d8919941c6e7efd3c7150eff7fe9d12afa631

llm-local: ## Serve the pinned local model on 127.0.0.1:8081 in the foreground (Ctrl-C stops it)
	uv run mlx_lm.server --host 127.0.0.1 --port 8081 --model "$$(uvx --from 'huggingface_hub==2.0.0' \
		hf download $(LLM_MODEL) --revision $(LLM_REVISION) --quiet)"

# --- API (M0-07) ---------------------------------------------------------------------------------
api: ## Run the API on :8000 (needs `make up` and `make migrate`)
	uv run uvicorn careflow_api.main:create_app --factory --host 127.0.0.1 --port 8000

migrate: ## Apply the Alembic migrations and the intake checkpoint tables as careflow_migrator
	cd services/api && uv run alembic upgrade head
	uv run careflow-api setup-checkpoints

seed-dev: ## Create the development clinics, staff and devices (secrets from .env)
	uv run careflow-api seed-dev

# --- Voice (M1-14) -------------------------------------------------------------------------------
voice-models: ## Download the voice models (~2 GB) into services/voice/models and check their sha256
	services/voice/scripts/download_models.sh

voice: ## Run the voice service on 127.0.0.1:8090 (Apple silicon; needs `make voice-models`)
	uv run --package careflow-voice --extra mlx-whisper careflow-voice

# --- Apps (M0-08) --------------------------------------------------------------------------------
mock: ## Serve the OpenAPI examples with Prism on :4010 (static examples, deterministic)
	pnpm exec prism mock packages/contracts/openapi.yaml --port 4010

e2e: ## Playwright smoke suites for kiosk, staff and companion (each starts its own mock and app)
	pnpm test:e2e
