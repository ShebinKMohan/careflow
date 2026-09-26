#!/usr/bin/env bash
# Make sure the Docker daemon answers. If it does not, start Docker Desktop with `open -a Docker`
# and poll for up to ENSURE_DOCKER_TIMEOUT_S (default 180) seconds. Never changes Docker settings.
# Exit 0 when Docker is up; non-zero with a clear message otherwise.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

timeout_s="${ENSURE_DOCKER_TIMEOUT_S:-180}"

docker_up() { with_timeout 15 docker info >/dev/null 2>&1; }

if docker_up; then
  echo "ensure_docker: Docker is running."
  exit 0
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ensure_docker: Docker is not reachable and this is not macOS; start the daemon yourself." >&2
  exit 1
fi

echo "ensure_docker: Docker is not running; starting Docker Desktop (open -a Docker)."
if ! open -a Docker; then
  echo "ensure_docker: could not launch Docker Desktop. Is it installed?" >&2
  exit 1
fi

start=$SECONDS
until docker_up; do
  if ((SECONDS - start >= timeout_s)); then
    echo "ensure_docker: Docker is still down after ${timeout_s}s. Skip Docker-marked tests" \
      "(requires_docker) with this reason and move to a non-Docker issue." >&2
    exit 1
  fi
  sleep 3
done
echo "ensure_docker: Docker is up after $((SECONDS - start))s."
