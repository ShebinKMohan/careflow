#!/usr/bin/env bash
# Run a command with its Playwright browser inside the pinned official image (M2-01):
#   scripts/playwright_image.sh <cmd> [args...]
#
# Screenshot baselines differ by OS and fonts, so the @rtl screenshots are rendered and compared by
# the browser in mcr.microsoft.com/playwright:v<version>-noble, at the repo's @playwright/test
# version. The test runner, the apps and Prism stay on this machine: the command gets
# PW_TEST_CONNECT_WS_ENDPOINT (the image's `playwright run-server`) and
# PW_TEST_CONNECT_EXPOSE_NETWORK=<loopback>, so the browser reaches this machine's localhost
# through that connection. The container is removed on exit.
#
# Environment overrides:
#   PW_IMAGE_PORT  the host port for the browser server (default 3710)
set -euo pipefail
source "$(dirname "$0")/lib.sh"
cd "$(dirname "$0")/.."

if (($# == 0)); then
  echo "usage: scripts/playwright_image.sh <cmd> [args...]" >&2
  exit 2
fi
scripts/ensure_docker.sh >/dev/null

version="$(node -p "require('./e2e/node_modules/@playwright/test/package.json').version")"
image="mcr.microsoft.com/playwright:v${version}-noble"
port="${PW_IMAGE_PORT:-3710}"
name="careflow-playwright-$port"

remove() { docker rm -f "$name" >/dev/null 2>&1 || true; }
trap remove EXIT
trap 'exit 130' INT TERM

remove
echo "playwright_image: starting the browser server in $image on 127.0.0.1:$port" >&2
docker run -d --rm --init --name "$name" -p "127.0.0.1:$port:3000" \
  --user pwuser --workdir /home/pwuser "$image" \
  /bin/sh -c "npx -y playwright@$version run-server --port 3000 --host 0.0.0.0" >/dev/null

# The server answers plain HTTP once it listens (the WebSocket upgrade comes with the tests).
for _ in $(seq 1 180); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:$port/" || true)"
  [[ "$code" != "000" ]] && break
  if ! docker inspect "$name" >/dev/null 2>&1; then
    echo "playwright_image: the browser server stopped; see docker logs" >&2
    exit 1
  fi
  sleep 1
done
if [[ "$code" == "000" ]]; then
  echo "playwright_image: the browser server did not answer on port $port" >&2
  docker logs "$name" 2>&1 | tail -20 >&2
  exit 1
fi

export PW_TEST_CONNECT_WS_ENDPOINT="ws://127.0.0.1:$port/"
export PW_TEST_CONNECT_EXPOSE_NETWORK="<loopback>"
export PLAYWRIGHT_CHANNEL=chromium # the image's own Chromium
"$@"
