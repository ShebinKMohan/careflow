# ADR-012: Local infrastructure (Postgres + pgvector + pg_textsearch, Valkey, HAPI FHIR)

## Status

Accepted, 26 Sep 2026 ([M0-05]).

## Context

CareFlow needs a clinic database (sessions, queue, later RAG) and a simulated hospital to read from and write back to, all on one 18 GB MacBook next to one local LLM. The issue asked for Postgres 17 with pgvector and pg_textsearch, Valkey 8 and HAPI FHIR 8.12.1 within 6 GB, and for seeded synthetic patients.

## Decision

- **HAPI FHIR image `hapiproject/hapi:v8.12.0-1`** (HAPI FHIR 8.12.0), pinned by digest. HAPI FHIR 8.12.1 exists as a library release (15 Sep 2026), but no server image for it had been published on 26 Sep 2026; v8.12.0-1 (2 Sep 2026) is the newest. It has a native arm64 manifest. The image is distroless, so readiness is checked by polling `/fhir/metadata` rather than by a container healthcheck.
- **pg_textsearch 1.4.0 is built from source** in a multi-stage Dockerfile on `pgvector/pgvector:0.8.6-pg17-bookworm`, from the release tarball verified by SHA-256, and loaded through `shared_preload_libraries` (its README requires it). No full-text-search fallback is needed: the extension builds and runs on PG17.
- **Least-privilege roles**: `careflow_migrator` owns the `careflow` schema, `careflow_app` has NOBYPASSRLS, and HAPI gets its own `hapi` role and database. Passwords come from the gitignored `.env` through psql `\getenv`; `gen_dev_env.py` also derives `DATABASE_URL`, `DATABASE_URL_MIGRATOR`, `REDIS_URL` and `FHIR_BASE_URL` from them.
- **Ports bind to 127.0.0.1 only**, and Valkey runs with `maxmemory 192mb` and `noeviction` under its 256m container limit, so event streams and tickets are never silently evicted.
- **Seeding is idempotent**: Synthea is skipped when HAPI already has at least 200 patients, personas are PUT with fixed ids, and the loader refuses any non-localhost FHIR server.

## Consequences

- When a HAPI 8.12.1 image is published, the pin moves to it (one line; the persona and Synthea data are unaffected).
- The first `make up` builds the Postgres image (about 4-5 minutes on the M3 Pro); later starts use the cached image.
- The default profile uses 3.75 GiB of the 6 GiB cap, which leaves about 2.25 GiB for the `hl7` or `obs` profile.
