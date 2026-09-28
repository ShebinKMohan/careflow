#!/usr/bin/env bash
# The nightly journeys (M2-12): the whole Playwright suite on this Mac against the live local model.
# GitHub's runners cannot run Qwen3.5-9B MLX, so CI replays recorded replies; this runs nothing
# recorded. It starts its own stack with E2E_LIVE=1 LOCAL_LLM=1 (scripts/with_llm.sh serves the
# pinned model on 8081, or reuses one already there), runs the suite, writes
# eval/reports/e2e_nightly.json (each spec's result, duration and provider, and the timings the
# journeys measured) and stops the stack it started. Voice stays off (one local model at a time)
# unless VOICE is set. Exit status: the suite's.
#
#   scripts/nightly-e2e.sh          # stack careflow-e2e, every port +30000 (CAREFLOW_STACK, CAREFLOW_PORT_OFFSET)
#   make install-nightly            # the owner's step: a LaunchAgent at 02:00 (README)
set -euo pipefail
cd "$(dirname "$0")/.."

export CAREFLOW_STACK="${CAREFLOW_STACK:-careflow-e2e}"
offset="${CAREFLOW_PORT_OFFSET:-30000}"
started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p .run

E2E_LIVE=1 LOCAL_LLM=1 VOICE="${VOICE:-0}" SEED_SYNTHEA=0 CAREFLOW_PORT_OFFSET="$offset" make up
trap 'scripts/down.sh' EXIT
served="$(curl -fsS http://127.0.0.1:8081/v1/models | python3 -c 'import json, sys; print(json.load(sys.stdin)["data"][0]["id"])')"

status=0
pnpm --filter @careflow/e2e exec playwright test || status=$?
uv run python scripts/nightly_report.py e2e/test-results/journeys.json \
  --model "$served" --started "$started" --stack "$CAREFLOW_STACK"
exit "$status"
