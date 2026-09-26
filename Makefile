# CareFlow developer entry points. Research prototype. Synthetic data only. Not for clinical use.
# `make install && make check` is the whole local gate; CI runs the same targets per language.

.DEFAULT_GOAL := help
SHELL := /bin/bash
# `uv run` must never re-resolve: a dependency added to a pyproject.toml without `uv lock` fails
# fast instead of being downloaded and installed before the banned-dependency check can run.
export UV_LOCKED := 1

.PHONY: help install install-py install-node lint lint-py lint-node typecheck typecheck-py \
	typecheck-node test test-py test-node banned-deps check contracts

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
	uv run --no-project --python 3.12 python scripts/check_banned_deps.py

check: banned-deps lint typecheck test ## Everything CI checks, except the secret and CVE scans

CONTRACTS := packages/contracts
CONTRACTS_PY := $(CONTRACTS)/src/careflow_contracts/generated

# Regenerate typed models from packages/contracts. Deterministic: CI checks `git diff --exit-code`.
contracts: ## Regenerate the Python models and alert templates from packages/contracts
	rm -rf $(CONTRACTS_PY)
	uv run datamodel-codegen --input $(CONTRACTS)/schemas --input-file-type jsonschema \
		--output $(CONTRACTS_PY) --output-model-type pydantic_v2.BaseModel \
		--target-python-version 3.12 --use-annotated --field-constraints --use-union-operator \
		--use-standard-collections --disable-timestamp --collapse-root-models --use-type-alias \
		--strict-nullable \
		--formatters ruff-format \
		--custom-file-header-mode prepend --custom-file-header-path $(CONTRACTS)/codegen/header.txt
	uv run python $(CONTRACTS)/codegen/gen_alert_templates.py
