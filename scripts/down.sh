#!/usr/bin/env bash
# `make down`: stop what `make up` started from this checkout: its processes, then its Docker
# services (data volumes stay). A stack another checkout started is left alone (scripts/stack.py
# checks Compose's working-dir label). CAREFLOW_STACK selects the stack (default `careflow`).
set -euo pipefail
cd "$(dirname "$0")/.."
stack="${CAREFLOW_STACK:-careflow}"
pidfiles=(".run/stacks/$stack.pid")
[[ "$stack" == "careflow" ]] && pidfiles+=(.run/honcho.pid)  # started before stacks had names
for pidfile in "${pidfiles[@]}"; do
  [[ -f "$pidfile" ]] || continue
  pid="$(cat "$pidfile")"
  if kill -0 "$pid" 2>/dev/null; then
    echo "down: stopping the $stack processes (honcho pid $pid)"
    kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 40); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$pidfile"
done
python3 scripts/stack.py down --stack "$stack"
