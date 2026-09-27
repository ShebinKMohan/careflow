# CareFlow developer entry points. Research prototype. Synthetic data only. Not for clinical use.
# `make install && make check` is the whole local gate; CI runs the same targets per language.

.DEFAULT_GOAL := help
SHELL := /bin/bash
# `uv run` must never re-resolve: a dependency added to a pyproject.toml without `uv lock` fails
# fast instead of being downloaded and installed before the banned-dependency check can run.
export UV_LOCKED := 1

.PHONY: help install install-py install-node lint lint-py lint-node typecheck typecheck-py \
	typecheck-node test test-py test-node banned-deps check contracts contracts-lint \
	contracts-drift contracts-breaking contracts-check up down infra infra-down test-stack \
	test-stack-down test-integration seed ps logs nuke \
	llm-local api migrate seed-dev mock e2e e2e-apps rtl rag-ingest voice-models voice

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
	uv run python scripts/i18n/backtranslate.py --check

lint-node:
	pnpm -r lint
	node scripts/check-physical-css.mjs

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

up: ## Start everything: services, migrations, seed data, the API and the apps (M1-19)
	scripts/up.sh

down: ## Stop everything `make up` started; named volumes keep the data
	scripts/down.sh

infra: ## Start only Postgres, Valkey and HAPI FHIR and wait until they are healthy (tests)
	scripts/ensure_docker.sh
	python3 scripts/gen_dev_env.py
	python3 scripts/check_mem_budget.py
	$(COMPOSE) up -d --build --wait postgres valkey
	$(COMPOSE) up -d hapi
	python3 scripts/wait_for_http.py http://localhost:8080/fhir/metadata 180

infra-down: ## Stop only the Docker services
	$(COMPOSE) down
test-stack: ## Start an isolated Postgres, Valkey and HAPI (project careflow-test, ports +10000) and seed the personas
	python3 scripts/stack.py up --stack careflow-test
test-stack-down: ## Stop the isolated test stack (only when this checkout started it); its data volumes stay
	python3 scripts/stack.py down --stack careflow-test
test-integration: ## Pytest for the API, intake and integration packages against the isolated test stack
	CAREFLOW_REQUIRE_DB=1 CAREFLOW_REQUIRE_REDIS=1 python3 scripts/stack.py run --stack careflow-test -- \
		uv run pytest services/api services/intake services/integration -rs

seed: ## Load Synthea patients (SEED_SYNTHEA=0 skips them) and the 7 personas; FORCE=1 reloads
	@if [ "$(SEED_SYNTHEA)" != "0" ]; then infra/seed/run_synthea.sh; fi
	uv run python infra/seed/load_fhir.py $(if $(filter 0,$(SEED_SYNTHEA)),--personas-only,)
	uv run python infra/seed/add_mock_ids.py

ps: ## Service status
	$(COMPOSE) ps

logs: ## Follow this checkout's processes (CAREFLOW_STACK, default careflow) after the last 200 lines of its Docker services
	@stack=$${CAREFLOW_STACK:-careflow}; docker compose -p $$stack logs --tail 200; \
		if [ -f .run/stacks/$$stack.log ]; then tail -n 200 -f .run/stacks/$$stack.log; fi

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
rag-ingest: ## Load the clinic handbook into rag_chunks for the dev clinics (Qwen3-Embedding-0.6B, ~1.2 GB once)
	for clinic in clinic-a clinic-b; do \
	  id=$$(uv run --no-sync python -c "from careflow_api.cli import dev_id; print(dev_id('$$clinic'))"); \
	  uv run --package careflow-rag --extra embeddings python -m careflow_rag.ingest services/rag/handbook --clinic $$id; \
	done

voice-models: ## Download the voice models (~2 GB) into services/voice/models and check their sha256
	services/voice/scripts/download_models.sh

voice: ## Run the voice service on 127.0.0.1:8090 (Apple silicon; needs `make voice-models`)
	uv run --package careflow-voice --extra mlx-whisper --extra arabic-speech careflow-voice

# --- Apps (M0-08) --------------------------------------------------------------------------------
mock: ## Serve the OpenAPI examples with Prism on :4010 (static examples, deterministic)
	pnpm exec prism mock packages/contracts/openapi.yaml --port 4010

e2e: ## The three MVP journeys (M1-19) on the running stack: start it with `E2E=1 make up` first
	pnpm --filter @careflow/e2e journeys
e2e-apps: ## Playwright suites for kiosk, staff and companion against Prism mocks (each starts its own)
	pnpm test:e2e

# The apps build and serve on E2E_PORT (default each app's dev port; set it beside a running stack).
# RTL_ARGS=--update-snapshots writes new baselines, from the same pinned browser.
rtl: ## The @rtl tests of the three apps; screenshots from the browser in the pinned Playwright image
	scripts/playwright_image.sh sh -c 'for app in kiosk staff companion; do \
	  pnpm --filter $$app exec playwright test --grep @rtl $(RTL_ARGS) || exit 1; done'
