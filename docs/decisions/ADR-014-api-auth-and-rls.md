# ADR-014: API authentication, roles and row-level security

## Status

Accepted, 26 Sep 2026 ([M0-07]).

## Context

Kiosks, boards, integration services, nurses, doctors and admins from several clinics share one API and one Postgres database. Clinic isolation must hold even when a query forgets a `WHERE clinic_id`, and least privilege must hold per role. The login and device-token endpoints have a chicken-and-egg problem: under forced RLS no row is visible until a clinic is known, but the clinic is what login finds out.

## Decision

- **The app connects as `careflow_app`** (LOGIN, NOSUPERUSER, NOBYPASSRLS, not a table owner); Alembic and `seed-dev` use `careflow_migrator`. Migrations grant table privileges explicitly (no default privileges), and `clinic` is read-only for the app.
- **Every clinic-scoped table** has `ENABLE` + `FORCE ROW LEVEL SECURITY` and the policy `clinic_isolation`: `clinic_id = NULLIF(current_setting('app.clinic_id', true), '')::uuid` for both `USING` and `WITH CHECK`. The request dependency `get_db()` opens one transaction and sets `app.clinic_id` from the token with `set_config(..., true)`. With no setting, nothing is visible (fail closed). `tests/test_rls.py` fails any future table that has `clinic_id` without forced RLS and the policy.
- **Two SELECT-only lookup policies for authentication**: `staff_user.login_lookup` (`email = app.auth_email`) and `device.device_token_lookup` (`id = app.auth_device_id`). The login and device-token handlers set exactly the value being checked, in their own transaction, so at most that one row becomes readable, and never writable. Rejected alternatives: a BYPASSRLS definer role (only a superuser could create it, outside the migrations); a login directory table outside RLS; a clinic field in the login body (not in the contract).
- **Tokens**: PyJWT HS256 with `sub`, `role`, `clinic_id`, `iat`, `exp`, `jti` (+ `device_id` / `session_id`), all required on decode. Lifetimes: staff 12 h, patient_session 2 h, kiosk_device 24 h, board 24 h, integration 1 h. Bearer header only; a token in a query string is ignored (WebSockets use one-time tickets, M1-11).
- **Roles** are checked with an explicit `require_roles(*roles)` (no rank ordering). Integration service accounts are `device` rows of kind `integration` and use `POST /v1/auth/device-token` (the contract's `device_type` gained `integration`).
- **Login** is rate limited to 5 per minute per IP (Redis/Valkey, with an in-process fallback). Unknown users cost one bcrypt check, like wrong passwords, and get the same 401. Errors are RFC 9457 problem details.
- **`/health`** reports `db`, `redis` and `fhir` as `ok` or `down` (contract extended; non-breaking) and is excluded from tracing noise. FastAPI's own OpenAPI endpoints are off: `packages/contracts/openapi.yaml` is the contract.

## Consequences

- Later routers depend on `get_principal()`, `require_roles()` and `get_db()`. A route that needs one patient session must also check `session_id` itself (M1-12): RLS separates clinics, not sessions.
- New tables must add grants, RLS and the policy in their migration; the guard test enforces it.
- The lookup policies are the only non-clinic path into `staff_user` and `device`; M5-07 reviews them with the threat model.
