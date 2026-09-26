# Shared helpers for repo shell scripts. Source it: `source "$(dirname "$0")/lib.sh"`.

CAREFLOW_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# with_timeout <seconds> <cmd> [args...]: gtimeout when installed, else the Python wrapper.
# Never bare `timeout` (macOS has none).
with_timeout() {
  local seconds="$1"
  shift
  if command -v gtimeout >/dev/null 2>&1; then
    gtimeout "$seconds" "$@"
  else
    python3 "$CAREFLOW_SCRIPTS_DIR/run_with_timeout.py" "$seconds" "$@"
  fi
}
