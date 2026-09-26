#!/usr/bin/env bash
# Generate the synthetic Synthea v4 population into infra/seed/output/fhir (gitignored).
# The jar comes only from the official Synthea GitHub release; its SHA-256 is recorded in
# infra/seed/synthea.sha256 on first download and verified on every later run.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
version="${SYNTHEA_VERSION:-v4.0.0}"
population="${SYNTHEA_POPULATION:-200}"
seed="${SYNTHEA_SEED:-20260926}"
cache="$here/.cache"
out="$here/output"
jar_name="synthea-with-dependencies-$version.jar"
jar="$cache/$jar_name"
record="$here/synthea.sha256"
jre="eclipse-temurin:21.0.12.1_1-jre-noble@sha256:817f192b914584dac49595ae662461250b21aaa31df8698feac767d08defb3d0"

sha256_of() { python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"; }

mkdir -p "$cache"
if [[ ! -f "$jar" ]]; then
  echo "run_synthea: downloading Synthea $version (about 200 MB) from the official release"
  curl -fsSL --retry 3 -o "$jar.part" \
    "https://github.com/synthetichealth/synthea/releases/download/$version/synthea-with-dependencies.jar"
  mv "$jar.part" "$jar"
fi

actual="$(sha256_of "$jar")"
if [[ -f "$record" ]]; then
  expected="$(awk -v n="$jar_name" '$2 == n {print $1}' "$record")"
  if [[ -z "$expected" ]]; then
    echo "$actual  $jar_name" >>"$record"
    echo "run_synthea: recorded SHA-256 for $jar_name"
  elif [[ "$expected" != "$actual" ]]; then
    echo "run_synthea: SHA-256 mismatch for $jar_name (expected $expected, got $actual)" >&2
    exit 1
  fi
else
  echo "$actual  $jar_name" >"$record"
  echo "run_synthea: recorded SHA-256 for $jar_name"
fi

if compgen -G "$out/fhir/*.json" >/dev/null && [[ "${FORCE:-0}" != "1" ]]; then
  echo "run_synthea: $out/fhir already has bundles; skipping generation (FORCE=1 regenerates)."
  exit 0
fi
rm -rf "$out"
mkdir -p "$out"
echo "run_synthea: generating $population patients (seed $seed)"
docker run --rm -v "$cache:/synthea:ro" -v "$out:/output" "$jre" \
  java -jar "/synthea/$jar_name" -p "$population" -s "$seed" \
  --exporter.baseDirectory /output \
  --exporter.fhir.export true \
  --exporter.hospital.fhir.export true \
  --exporter.practitioner.fhir.export true \
  --exporter.years_of_history 5
echo "run_synthea: $(find "$out/fhir" -name '*.json' | wc -l | tr -d ' ') bundles in $out/fhir"
