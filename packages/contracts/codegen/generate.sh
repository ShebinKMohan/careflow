#!/usr/bin/env bash
# Regenerate every typed artefact from packages/contracts (run via `make contracts`).
# Deterministic: CI regenerates and fails on any diff. Never edit the outputs by hand.
set -euo pipefail

root="$(cd "$(dirname "$0")/../../.." && pwd)"
contracts="$root/packages/contracts"
out="$contracts/src/careflow_contracts/generated"
pkg=careflow_contracts.generated
cd "$root"

flags=(
  --output-model-type pydantic_v2.BaseModel --target-python-version 3.12 --use-annotated
  --field-constraints --use-union-operator --use-standard-collections --disable-timestamp
  --use-type-alias --strict-nullable --skip-root-model
  --formatters ruff-format
  --custom-file-header-mode prepend --custom-file-header-path "$contracts/codegen/header.txt"
)

# External $refs import the models generated from the referenced file instead of duplicating them.
schema_map=()
for f in "$contracts"/schemas/*.schema.json; do
  module="$(basename "$f" .schema.json | tr '-' '_')_schema"
  schema_map+=("$f=$pkg.$module")
done
event_map=()
for f in "$contracts"/events/*.schema.json; do
  module="$(basename "$f" .schema.json | tr '-' '_')_schema"
  event_map+=("$f=$pkg.events.$module")
done

rm -rf "$out"

# 1. M0-02 schemas -> generated/*_schema.py
uv run datamodel-codegen --input "$contracts/schemas" --input-file-type jsonschema \
  --output "$out" "${flags[@]}"

# 2. Event data schemas -> generated/events/*_schema.py (one file at a time: events/ also holds docs)
mkdir -p "$out/events"
cp "$contracts/codegen/header.txt" "$out/events/__init__.py"
for f in "$contracts"/events/*.schema.json; do
  module="$(basename "$f" .schema.json | tr '-' '_')_schema"
  uv run datamodel-codegen --input "$f" --input-file-type jsonschema \
    --output "$out/events/$module.py" "${flags[@]}" --external-ref-mapping "${schema_map[@]}"
done

# 3. OpenAPI components -> careflow_contracts/api_models.py
uv run datamodel-codegen --input "$contracts/openapi.yaml" --input-file-type openapi \
  --output "$contracts/src/careflow_contracts/api_models.py" "${flags[@]}" \
  --external-ref-mapping "${schema_map[@]}" "${event_map[@]}"

# 4. Alert templates -> generated/alert_templates.py
uv run python "$contracts/codegen/gen_alert_templates.py"

# 5. TypeScript types -> packages/contracts/ts/openapi.d.ts
pnpm --filter @careflow/contracts exec openapi-typescript openapi.yaml -o ts/openapi.d.ts --silent
