# CareFlow developer entry points. Research prototype. Synthetic data only. Not for clinical use.
# `make install && make check` is the whole local gate; CI runs the same targets per language.

.DEFAULT_GOAL := help
SHELL := /bin/bash

.PHONY: help install install-py install-node lint lint-py lint-node typecheck typecheck-py \
	typecheck-node test test-py test-node banned-deps check

help: ## List the targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-16s %s\n", $$1, $$2}'

install: install-py install-node ## Install locked Python and Node dependencies

install-py:
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

banned-deps: ## Fail on LiteLLM or any unconstrained Python dependency
	uv run python scripts/check_banned_deps.py

check: lint typecheck test banned-deps ## Everything CI checks, except the secret and CVE scans
