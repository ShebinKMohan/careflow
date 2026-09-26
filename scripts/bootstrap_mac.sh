#!/usr/bin/env bash
# Install the Mac developer tools CareFlow needs. Idempotent: a second run changes nothing.
# Never uses sudo and never changes system settings. Python packages come from uv.
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "bootstrap_mac.sh: macOS only." >&2
  exit 1
fi
if ! command -v brew >/dev/null 2>&1; then
  echo "bootstrap_mac.sh: Homebrew is required (https://brew.sh). Install it yourself; this script never uses sudo." >&2
  exit 1
fi

# formula:command-it-provides
tools=(pnpm:pnpm mkcert:mkcert osv-scanner:osv-scanner gitleaks:gitleaks rustup:rustup coreutils:gtimeout)
missing=()
for entry in "${tools[@]}"; do
  formula="${entry%%:*}"
  cmd="${entry##*:}"
  if command -v "$cmd" >/dev/null 2>&1 || brew list --formula "$formula" >/dev/null 2>&1; then
    continue
  fi
  missing+=("$formula")
done

if ((${#missing[@]})); then
  echo "bootstrap_mac.sh: installing ${missing[*]}"
  HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 brew install "${missing[@]}"
else
  echo "bootstrap_mac.sh: Homebrew tools already present."
fi

# Rust toolchain for the Tauri kiosk shell (M3-06). Homebrew's rustup is keg-only and no longer
# ships rustup-init, so its own binary installs the toolchain. Nothing edits shell profiles.
rustup_bin="$(brew --prefix rustup)/bin"
if command -v rustc >/dev/null 2>&1 || [[ -x "$HOME/.cargo/bin/rustc" ]] \
  || "$rustup_bin/rustup" which rustc >/dev/null 2>&1; then
  echo "bootstrap_mac.sh: Rust toolchain already present."
elif command -v rustup-init >/dev/null 2>&1; then
  rustup-init -y --no-modify-path
else
  "$rustup_bin/rustup" toolchain install stable --profile minimal
  "$rustup_bin/rustup" default stable
fi

echo "bootstrap_mac.sh: done."
