#!/usr/bin/env bash
# Check what `make up` needs before it starts anything, with a clear message for each gap:
# Docker (started if Docker Desktop is installed), uv, pnpm, Node >= 24 and free ports.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

fail=0
need() {  # need <command> <how to install>
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "check_prereqs: '$1' is missing. $2" >&2
    fail=1
  fi
}

need docker "Install Docker Desktop: https://www.docker.com/products/docker-desktop/"
need uv "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
need pnpm "Install pnpm 12 (corepack enable, or https://pnpm.io/installation)."
need node "Install Node 24 or newer: https://nodejs.org/"
if command -v node >/dev/null 2>&1; then
  major="$(node -p 'process.versions.node.split(".")[0]')"
  if ((major < 24)); then
    echo "check_prereqs: Node $major found; CareFlow needs Node 24 or newer." >&2
    fail=1
  fi
fi
((fail == 0)) || exit 1

"$CAREFLOW_SCRIPTS_DIR/ensure_docker.sh"

busy=0
for port in ${CAREFLOW_PORTS:-8000 3000 3001 3002}; do
  if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "check_prereqs: port $port is in use ($(lsof -iTCP:"$port" -sTCP:LISTEN -Fc 2>/dev/null | sed -n 's/^c//p' | head -1)). Stop it, or run 'make down' first." >&2
    busy=1
  fi
done
((busy == 0)) || exit 1
echo "check_prereqs: Docker, uv, pnpm and Node $(node -v) are ready; the ports are free."
