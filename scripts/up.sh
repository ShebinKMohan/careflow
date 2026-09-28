#!/usr/bin/env bash
# `make up`: the whole CareFlow stack with one command (M1-19).
#
# Checks the prerequisites, starts Postgres, Valkey and HAPI FHIR, installs the locked
# dependencies, migrates, seeds the personas and the demo staff, makes sure an urgency model is
# installed, then runs the API and the three apps (and the voice service when its models are
# downloaded, and the local LLM when LOCAL_LLM=1) under honcho in the background. It returns when
# every process answers and the API's database, Valkey and FHIR server are reachable ("CareFlow
# is up"). `make logs` follows them; `make down` stops what this checkout started.
#
#   CAREFLOW_STACK=name CAREFLOW_PORT_OFFSET=n
#                      run a second, isolated stack: its own Compose project and every port + n
#                      (scripts/stack.py). Default: the dev stack `careflow` on the CONVENTIONS ports.
#   VOICE=0            leave the voice service out (default: on when `make voice-models` has run
#                      and the stack's voice port is free)
#   LOCAL_LLM=1        serve pinned Qwen3.5-9B on :8081; CAREFLOW_LLM_MODEL selects pinned 4B
#   SEED_SYNTHEA=0     load the 7 personas only (no Synthea population)
#   E2E=1              deterministic journeys (make e2e): every model reply is replayed from
#                      e2e/replays/providers.cassette.json, the intake uses e2e/config/intake.yaml
#                      and the queue e2e/config/rooms.yaml (a 5 s grace window, test config only)
#   E2E_RECORD=1       record that cassette from the local model instead; run the journeys with the
#                      model up: `scripts/with_llm.sh make e2e` (or LOCAL_LLM=1 here)
#   E2E_RECORD=add     keep the recorded replies (replayed, never re-recorded) and record only the
#                      prompts the cassette lacks: a new journey's
#   E2E_LIVE=1         the journeys against the local model, nothing replayed (same model rule)
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/lib.sh

stack="${CAREFLOW_STACK:-careflow}"
run_dir=.run/stacks
pidfile="$run_dir/$stack.pid"
mkdir -p "$run_dir"
if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
  echo "up: stack $stack is already running from this checkout (pid $(cat "$pidfile")); 'make down' first." >&2
  exit 1
fi
for other in "$run_dir"/*.pid; do  # one set of apps per checkout: they share .next
  [[ -f "$other" && "$other" != "$pidfile" ]] || continue
  if kill -0 "$(cat "$other")" 2>/dev/null; then
    echo "up: stack $(basename "$other" .pid) already runs its apps from this checkout; stop it first." >&2
    exit 1
  fi
done

python3 scripts/gen_dev_env.py
if [[ -n "${CAREFLOW_PORT_OFFSET:-}" ]]; then
  env_file="$(python3 scripts/stack.py env --stack "$stack" --offset "$CAREFLOW_PORT_OFFSET")"
else  # the stack's default: +0 for the dev stack, +10000 for careflow-test
  env_file="$(python3 scripts/stack.py env --stack "$stack")"
fi
set -a
# shellcheck disable=SC1090
source "$env_file"
set +a
offset="$CAREFLOW_PORT_OFFSET"

voice_port="$VOICE_PORT"
CAREFLOW_PORTS="$CAREFLOW_API_PORT $CAREFLOW_KIOSK_PORT $CAREFLOW_STAFF_PORT $CAREFLOW_COMPANION_PORT" \
  scripts/check_prereqs.sh
python3 scripts/check_mem_budget.py
python3 scripts/stack.py up --stack "$stack" --offset "$offset" --no-seed

extras=()
voice_extras=()
if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
  # The voice service's local STT, and its Arabic speech (M2-02); Apple silicon only.
  voice_extras=(--extra mlx-whisper --extra arabic-speech)
  # Plus the handbook's local embeddings (careflow-rag, M2-08). Only the sync takes them: the
  # voice service's `uv run --package careflow-voice` refuses an extra its package lacks.
  extras=("${voice_extras[@]}" --extra embeddings)
fi
# --inexact: never remove what another extra installed (e.g. the laya fine-tuning stack).
uv sync --locked --all-packages --inexact "${extras[@]}"
pnpm install --frozen-lockfile
make migrate
make seed
make seed-dev
uv run python scripts/ensure_triage_model.py

# E2E: what the API replays (or records) and how the intake phrases its questions.
cassette=e2e/replays/providers.cassette.json
model_note="run the journeys with the model up: scripts/with_llm.sh make e2e"
if [[ "${E2E_RECORD:-0}" == "1" || "${E2E_RECORD:-0}" == "add" ]]; then
  [[ "${LOCAL_LLM:-0}" == "1" ]] || echo "up: E2E_RECORD=$E2E_RECORD without LOCAL_LLM=1: $model_note"
  # 1 records the cassette afresh; add keeps its replies and adds the new prompts' (a new journey).
  # add: replay what the cassette holds, record only the prompts it lacks (never re-record).
  if [[ "$E2E_RECORD" == "add" ]]; then mode=add; else mode=record; rm -f "$cassette"; fi
  export CAREFLOW_PROVIDER_MODE=$mode CAREFLOW_CASSETTE="$PWD/$cassette"
  export CAREFLOW_INTAKE_CONFIG="$PWD/e2e/config/intake.yaml"
  export CAREFLOW_ROOMS_CONFIG="$PWD/e2e/config/rooms.yaml"  # a 5 s grace window: the no-show journey
  echo "up: E2E recording: every model reply goes to $cassette"
elif [[ "${E2E_LIVE:-0}" == "1" ]]; then
  [[ "${LOCAL_LLM:-0}" == "1" ]] || echo "up: E2E_LIVE=1 without LOCAL_LLM=1: $model_note"
  export CAREFLOW_INTAKE_CONFIG="$PWD/e2e/config/intake.yaml"
  export CAREFLOW_ROOMS_CONFIG="$PWD/e2e/config/rooms.yaml"  # a 5 s grace window: the no-show journey
  echo "up: E2E live: the journeys use the local model, nothing is replayed"
elif [[ "${E2E:-0}" == "1" ]]; then
  if [[ ! -s "$cassette" ]]; then
    echo "up: E2E=1 replays $cassette, which is missing; record it: E2E_RECORD=1 LOCAL_LLM=1 make up, then make e2e" >&2
    exit 1
  fi
  export CAREFLOW_PROVIDER_MODE=replay CAREFLOW_CASSETTE="$PWD/$cassette"
  export CAREFLOW_INTAKE_CONFIG="$PWD/e2e/config/intake.yaml"
  export CAREFLOW_ROOMS_CONFIG="$PWD/e2e/config/rooms.yaml"  # a 5 s grace window: the no-show journey
  echo "up: E2E replay: model replies come from $cassette, no model is called"
fi

procfile="$run_dir/$stack.Procfile"
cp Procfile.dev "$procfile"
services=(
  api="http://127.0.0.1:$CAREFLOW_API_PORT/health"
  kiosk="http://localhost:$CAREFLOW_KIOSK_PORT"
  staff="http://localhost:$CAREFLOW_STAFF_PORT"
  companion="http://localhost:$CAREFLOW_COMPANION_PORT"
)

if [[ "${VOICE:-auto}" == "0" ]]; then
  echo "up: voice service off (VOICE=0)."
elif [[ -z "$(ls -A services/voice/models 2>/dev/null)" ]]; then
  echo "up: voice service off: its models are not downloaded ('make voice-models', ~2 GB)."
elif lsof -iTCP:"$voice_port" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "up: voice service off: port $voice_port is in use (set CAREFLOW_PORT_OFFSET or VOICE=0)."
else
  echo "voice: VOICE_PORT=$voice_port uv run --package careflow-voice ${voice_extras[*]} careflow-voice" >>"$procfile"
  services+=("voice=http://127.0.0.1:$voice_port/healthz")
  # With a voice service, both apps show consented hold-to-talk on secure origins. The phone
  # explains the HTTPS requirement on a plain HTTP LAN URL. The public flag is expanded by
  # honcho's shell after .env is loaded, so an empty value there counts as unset.
  sed -i.orig \
    -e 's|^kiosk: |kiosk: NEXT_PUBLIC_VOICE_ENABLED=${NEXT_PUBLIC_VOICE_ENABLED:-true} |' \
    -e 's|^companion: |companion: NEXT_PUBLIC_VOICE_ENABLED=${NEXT_PUBLIC_VOICE_ENABLED:-true} |' "$procfile"
  rm -f "$procfile.orig"
fi

wait_s=300
if [[ "${LOCAL_LLM:-0}" == "1" ]]; then
  # The one sanctioned way to start the local model (docs/AGENT_RUNBOOK.md section 6): with_llm.sh
  # serves the pinned Qwen3.5-9B on 8081, or reuses a server already answering there, and stops
  # only a server it started. honcho signals the whole process group on `make down`.
  echo 'llm: CAREFLOW_LLM_MODEL=${CAREFLOW_LLM_MODEL:-mlx-community/Qwen3.5-9B-MLX-4bit} scripts/with_llm.sh tail -f /dev/null' >>"$procfile"
  services+=("llm=http://127.0.0.1:8081/v1/models")
  # Uvicorn does not accept requests until fixed EN/AR examples pass real extraction.
  sed -i.orig 's|^api: |api: CAREFLOW_LLM_MODEL=${CAREFLOW_LLM_MODEL:-mlx-community/Qwen3.5-9B-MLX-4bit} CAREFLOW_WARM_EXTRACTION=1 |' "$procfile"
  rm -f "$procfile.orig"
  wait_s=900  # a first download of the model (~5 GB)
fi

echo "up: stack $stack: starting $(grep -c '^[a-z]*:' "$procfile") processes (logs: make logs)"
# honcho loads its env files over the environment: the stack's file comes last, so its ports and
# URLs win over the dev values in .env (secrets stay from .env).
nohup uv run honcho -e ".env,$env_file" start -f "$procfile" >"$run_dir/$stack.log" 2>&1 &
echo $! >"$pidfile"
if ! python3 scripts/wait_healthy.py "${services[@]}" --timeout "$wait_s"; then
  tail -n 40 "$run_dir/$stack.log" >&2 || true
  exit 1
fi
