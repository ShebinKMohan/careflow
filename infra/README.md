# Infra

Local stand-ins for the clinic server and the hospital, started with Docker Compose (`infra/docker-compose.yml`, project name `careflow`). Research prototype. Synthetic data only. Not for clinical use.

```bash
make up      # start Postgres, Valkey and HAPI FHIR and wait until healthy
make seed    # Synthea patients (SEED_SYNTHEA=0 skips them) + the 7 demo personas
make ps      # status
make logs    # last 200 lines per service
make down    # stop; named volumes keep the data
```

`make up` starts Docker Desktop if needed (`scripts/ensure_docker.sh`), creates any missing local secret in the gitignored `.env` (`scripts/gen_dev_env.py`), checks the memory budget, builds the Postgres image and waits for the healthchecks and for `http://localhost:8080/fhir/metadata`. Agents never run `make nuke` (it deletes every volume after an interactive confirmation); to reseed they run `make seed FORCE=1`.

## Services (default profile)

| Service | Image | Port (127.0.0.1 only) | mem_limit | Health |
|---|---|---|---|---|
| postgres | `careflow/postgres` built from `pgvector/pgvector:0.8.6-pg17-bookworm` + pg_textsearch 1.4.0 | 5432 | 1g | `pg_isready` |
| valkey | `valkey/valkey:8.1.10` | 6379 | 256m | `valkey-cli ping` |
| hapi | `hapiproject/hapi:v8.12.0-1` (HAPI FHIR 8.12.0, R4) | 8080 (`/fhir`) | 2560m | `make up` polls `/fhir/metadata` (the image is distroless: no shell, no curl) |

Every image is pinned by digest. HAPI's image has a native arm64 manifest, so no `platform:` override is needed on Apple silicon. No image exists yet for HAPI FHIR 8.12.1 (the core library was released on 15 Sep 2026; the newest server image, published on 2 Sep 2026, is v8.12.0-1), so the stack runs 8.12.0 (ADR-012).

**Memory budget:** the default services total 3.75 GiB, under the 6 GiB cap that leaves room for one local LLM in 18 GB of RAM. `python3 scripts/check_mem_budget.py` sums `mem_limit` over the default-profile services and fails over budget or when a service has no limit. Don't raise the limits.

## Postgres

- Databases `careflow` (owned by `careflow_migrator`) and `hapi` (owned by `hapi`).
- Roles: `careflow_migrator` owns the schema (Alembic, M0-07); `careflow_app` is the API's runtime role (LOGIN, NOSUPERUSER, NOBYPASSRLS); `hapi` is HAPI's own role. Passwords come from `.env` through `\getenv` in `postgres/init/001-init.sql`, which runs once on an empty volume.
- Extensions in `careflow`: `vector` (pgvector 0.8.6) and `pg_textsearch` (BM25; loaded through `shared_preload_libraries`). Example: `CREATE INDEX ... USING bm25(body) WITH (text_config='english')`, then `ORDER BY body <@> 'query'`.
- Connection strings for code: `DATABASE_URL` (as `careflow_app`), `DATABASE_URL_MIGRATOR`, `REDIS_URL`, `FHIR_BASE_URL` (derived in `.env` by `gen_dev_env.py`).

## Seed data (`infra/seed/`)

- `run_synthea.sh`: downloads the Synthea v4.0.0 jar from the official GitHub release into `.cache/` (gitignored), records its SHA-256 in `synthea.sha256` on first download and verifies it afterwards, then generates 200 patients (seed 20260926, 5 years of history) inside `eclipse-temurin:21` JRE into `output/` (gitignored).
- `load_fhir.py`: refuses any FHIR server that is not localhost; posts the hospital and practitioner bundles first, then every patient bundle as a transaction; skips Synthea when the server already has at least 200 patients (unless `FORCE=1`); then upserts the personas.
- `personas.yaml` and `personas/<id>.json`: the seven synthetic demo personas (chest pain, stroke, drowsy child, sepsis, routine, QR phone, no-show), each with a keypad ID in `Patient.identifier` (system `https://careflow.example/fhir/sid/keypad-id`) and FHIR id `persona-<id>`. The bundles PUT fixed ids, so seeding twice changes nothing; `__TODAY__` becomes the seeding date (appointments booked today).

## Reserved profiles

- `hl7` (M4-01): Open Integration Engine, about 768m.
- `obs` (M5-05): Langfuse and its workers.

Each must fit under the 6 GiB cap together with the default services; running `obs` may mean stopping HAPI first (`docker compose stop hapi`). Stop services the current issue does not need.
