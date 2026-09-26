#!/usr/bin/env bash
# Run a command with the local LLM available: `scripts/with_llm.sh <cmd> [args...]`.
#
# Starts mlx_lm.server (OpenAI-compatible) on 127.0.0.1:8081 if nothing answers there, waits for
# /v1/models (up to WITH_LLM_WAIT_S, default 600 s, for a first download), runs the command, then
# stops only a server it started. The only sanctioned way to start the local model: one model
# loaded at a time (docs/AGENT_RUNBOOK.md section 6).
#
# Environment overrides:
#   CAREFLOW_LLM_MODEL     HF repo to serve (default mlx-community/Qwen3.5-9B-MLX-4bit)
#   CAREFLOW_LLM_REVISION  pinned revision; the snapshot is downloaded and served from disk
#   MLX_PORT               port (default 8081)
#   WITH_LLM_WAIT_S        readiness timeout in seconds (default 600)
# The command sees CAREFLOW_LOCAL_LLM_BASE_URL=http://127.0.0.1:<port>/v1.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

if (($# == 0)); then
  echo "usage: scripts/with_llm.sh <cmd> [args...]" >&2
  exit 2
fi

host=127.0.0.1
port="${MLX_PORT:-8081}"
model="${CAREFLOW_LLM_MODEL:-mlx-community/Qwen3.5-9B-MLX-4bit}"
revision="${CAREFLOW_LLM_REVISION:-}"
mlx_lm_version="${MLX_LM_VERSION:-0.31.3}"
hf_hub_version="${HF_HUB_VERSION:-2.0.0}"
wait_s="${WITH_LLM_WAIT_S:-600}"
log="${WITH_LLM_LOG:-${TMPDIR:-/tmp}/careflow-mlx-lm-server.log}"
base_url="http://$host:$port"

server_up() { curl -fsS --max-time 3 "$base_url/v1/models" >/dev/null 2>&1; }

started_pid=""
stop_server() {
  if [[ -n "$started_pid" ]] && kill -0 "$started_pid" 2>/dev/null; then
    echo "with_llm: stopping mlx_lm.server (pid $started_pid)" >&2
    pkill -TERM -P "$started_pid" 2>/dev/null || true
    kill -TERM "$started_pid" 2>/dev/null || true
    for _ in {1..20}; do
      kill -0 "$started_pid" 2>/dev/null || break
      sleep 0.5
    done
    pkill -KILL -P "$started_pid" 2>/dev/null || true
    kill -KILL "$started_pid" 2>/dev/null || true
  fi
}
trap stop_server EXIT
trap 'exit 130' INT TERM

if server_up; then
  echo "with_llm: reusing the server already answering on $base_url" >&2
else
  if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "with_llm: mlx_lm.server needs Apple silicon; use the replay provider on this machine." >&2
    exit 1
  fi
  model_ref="$model"
  if [[ -n "$revision" ]]; then
    model_ref="$(with_timeout "$wait_s" uvx --from "huggingface_hub==$hf_hub_version" \
      hf download "$model" --revision "$revision" --quiet)"
  fi
  echo "with_llm: starting mlx_lm.server ($model${revision:+@$revision}) on $base_url; log $log" >&2
  uvx --from "mlx-lm==$mlx_lm_version" mlx_lm.server \
    --model "$model_ref" --host "$host" --port "$port" >"$log" 2>&1 &
  started_pid=$!
  start=$SECONDS
  until server_up; do
    if ! kill -0 "$started_pid" 2>/dev/null; then
      echo "with_llm: mlx_lm.server exited early; last log lines:" >&2
      tail -n 20 "$log" >&2 || true
      exit 1
    fi
    if ((SECONDS - start >= wait_s)); then
      echo "with_llm: mlx_lm.server not ready after ${wait_s}s (log: $log)" >&2
      exit 1
    fi
    sleep 2
  done
  echo "with_llm: server ready after $((SECONDS - start))s" >&2
fi

export CAREFLOW_LOCAL_LLM_BASE_URL="$base_url/v1"
set +e
"$@"
status=$?
set -e
exit "$status"
