#!/usr/bin/env bash
# Fail when packages/contracts/openapi.yaml breaks the API compared with a base ref
# (CONTRACTS_BASE_REF, default origin/main). Skips when the base has no spec yet.
# The whole contracts folder is extracted so the spec's relative $refs resolve.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
base_ref="${CONTRACTS_BASE_REF:-origin/main}"
spec=packages/contracts/openapi.yaml

if ! git cat-file -e "$base_ref:$spec" 2>/dev/null; then
  echo "contracts_breaking: $base_ref has no $spec yet; skipping the breaking-change check."
  exit 0
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git archive "$base_ref" packages/contracts | tar -x -C "$tmp"
echo "contracts_breaking: comparing $spec with $base_ref"
ignore=packages/contracts/oasdiff-err-ignore.txt  # reviewed, intentional changes only
oasdiff breaking "$tmp/$spec" "$spec" --fail-on ERR --err-ignore "$ignore"
