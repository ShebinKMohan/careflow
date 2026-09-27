#!/usr/bin/env bash
# `make up`: the whole CareFlow stack with one command (M1-19).
#
# Checks the prerequisites, starts Postgres, Valkey and HAPI FHIR, installs the locked
# dependencies, migrates, seeds the personas and the demo staff, makes sure an urgency model is
# installed, then runs the API and the three apps (and the voice service when its models are
# downloaded, and the local LLM when LOCAL_LLM=1) under honcho in the background. It returns when
# every process answers ("CareFlow is up"). `make logs` follows them; `make down` stops everything.
#
#   VOICE=0            leave the voice service out (default: on when `make voice-models` has run
#                      and VOICE_PORT, default 8090, is free)
#   LOCAL_LLM=1        also serve the pinned Qwen3.5-9B MLX model on :8081
#   SEED_SYNTHEA=0     load the 7 personas only (no Synthea population)
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/lib.sh

run_dir=.run
pidfile="$run_dir/honcho.pid"
if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
  echo "up: CareFlow is already running (pid $(cat "$pidfile")); 'make down' first." >&2
  exit 1
fi

scripts/check_prereqs.sh
make infra
extras=()
if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
  # The voice service's local STT, and its Arabic speech (M2-02); Apple silicon only.
  extras=(--extra mlx-whisper --extra arabic-speech)
fi
uv sync --locked --all-packages "${extras[@]}"
pnpm install --frozen-lockfile
make migrate
make seed
make seed-dev
uv run python scripts/ensure_triage_model.py

mkdir -p "$run_dir"
procfile="$run_dir/Procfile"
cp Procfile.dev "$procfile"
services=(
  api=http://127.0.0.1:8000/health
  kiosk=http://localhost:3000
  staff=http://localhost:3001
  companion=http://localhost:3002
)

voice_port="${VOICE_PORT:-8090}"
if [[ "${VOICE:-auto}" == "0" ]]; then
  echo "up: voice service off (VOICE=0)."
elif [[ -z "$(ls -A services/voice/models 2>/dev/null)" ]]; then
  echo "up: voice service off: its models are not downloaded ('make voice-models', ~2 GB)."
elif lsof -iTCP:"$voice_port" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "up: voice service off: port $voice_port is in use (set VOICE_PORT)."
else
  echo "voice: VOICE_PORT=$voice_port uv run --package careflow-voice ${extras[*]} careflow-voice" >>"$procfile"
  services+=("voice=http://127.0.0.1:$voice_port/healthz")
fi

if [[ "${LOCAL_LLM:-0}" == "1" ]]; then
  model="$(with_timeout 600 uvx --from 'huggingface_hub==2.0.0' hf download \
    mlx-community/Qwen3.5-9B-MLX-4bit --revision 938d8919941c6e7efd3c7150eff7fe9d12afa631 --quiet)"
  echo "llm: uv run mlx_lm.server --host 127.0.0.1 --port 8081 --model $model" >>"$procfile"
  services+=("llm=http://127.0.0.1:8081/v1/models")
fi

echo "up: starting $(grep -c '^[a-z]*:' "$procfile") processes (logs: make logs)"
nohup uv run honcho start -f "$procfile" >"$run_dir/careflow.log" 2>&1 &
echo $! >"$pidfile"
if ! python3 scripts/wait_healthy.py "${services[@]}" --timeout 300; then
  tail -n 40 "$run_dir/careflow.log" >&2 || true
  exit 1
fi
