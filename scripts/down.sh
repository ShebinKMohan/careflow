#!/usr/bin/env bash
# `make down`: stop the processes `make up` started, then the Docker services (data volumes stay).
set -euo pipefail
cd "$(dirname "$0")/.."
pidfile=.run/honcho.pid
if [[ -f "$pidfile" ]]; then
  pid="$(cat "$pidfile")"
  if kill -0 "$pid" 2>/dev/null; then
    echo "down: stopping the CareFlow processes (honcho pid $pid)"
    kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 40); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$pidfile"
fi
make infra-down
