# Phone session recovery during takeover

Date: 27 Sep 2026. Research prototype. Synthetic data only. Not for clinical use.

Scope: companion recovery, authenticated API checkpoint/revision handling,
generated contracts and compatible voice request serialization in the isolated
`careflow-takeover` worktree, based on `adb8908186b0ef5968ef88950356ec6c8d562436`.
The primary `careflow` checkout and its in-progress Laya changes were not edited.
This is local verification evidence, not a deployment or whole-milestone claim.

## Behavior changed

- The QR landing page and companion start page resume an existing tab-scoped
  phone session after reload. They read the current question and revision from
  `GET /v1/sessions/{session_id}/status`; they do not persist questions, answers,
  transcripts or cached intake checkpoints in browser storage.
- Successful QR claims always read the server checkpoint, including on the first
  render. React Strict Mode effect replay shares the initial promise and does not
  claim a one-time code twice. The QR fragment is removed from the address bar.
- Claim and turn HTTP errors and thrown network errors now lead to visible,
  recoverable states. After a claim has yielded a saved credential, retry reads
  the saved visit rather than reusing the consumed code.
- Every phone answer includes the server's `expected_intake_revision`. An
  uncertain turn response is reconciled with the server before another answer is
  enabled. Revision changes, including side questions that reuse a question ID,
  advance the UI. A stale-revision 409 reads the accepted checkpoint without
  automatically reposting the old answer. Atomic enforcement is implemented in
  the API by the coordinating backend subtask.
- An intake with a missing question or invalid/missing revision fails visibly
  with retry; it is not presented as complete. Expired, forbidden, missing,
  closed or terminal no-show sessions clear the local credential and offer
  reconnection. Called and arrived sessions open the fixed `/status` route immediately without
  showing waiting instructions. Other completed-intake states offer the queue
  status link.
- Reconnecting after an expired turn explicitly resets the parent visit state
  and shows the code form, including when the manual flow already occupies `/`.
  POST response revisions must be integers greater than or equal to zero, just
  like checkpoint revisions; invalid revisions trigger reconciliation.
- `E2E_PORT` can select a free companion test-app port; its default remains 3002.

## Checks run

Commands ran from the isolated worktree root, with locked dependencies installed.

| Command | Observed result |
| --- | --- |
| `pnpm install --frozen-lockfile` | Successful; no root dependency or lockfile change |
| `pnpm -F companion test` | 2 test files passed; **26 tests passed** (24 recovery cases plus 2 existing layout/i18n checks) |
| `pnpm -F companion typecheck` | Next route type generation and TypeScript passed against regenerated revision contracts |
| `pnpm -F companion lint` | ESLint passed |
| `E2E_PORT=3102 pnpm -F companion test:e2e` | Production build succeeded; **9 browser tests passed** (5 recovery journeys plus 4 existing smoke/PWA checks), no retries |
| `git diff --check -- apps/companion` | Passed |

Before the reviewer follow-ups, the first browser run had six passes and one test-locator ambiguity: Next's empty
route announcer also has `role=alert`. The locator was scoped to the expected
error text; the next complete run passed all seven. After adding manual reconnect
and called-state regressions, the complete suite passed all nine. No product assertion was
removed and no retry policy was added.

The test application used port 3102 and Prism used port 4012, both checked free
before startup. Playwright managed their shutdown. The existing smoke suite's
regenerated historical screenshot was restored, so this subtask does not rewrite
earlier evidence.

## What the evidence proves

The browser tests exercise actual production React/Next code, navigation,
sessionStorage and page reloads in Chrome. API responses are controlled by
Playwright routing or Prism contract examples. They prove browser recovery
behavior and request shapes; they do **not** prove a real phone/network handoff,
Postgres persistence, local model behavior, FHIR writes or the complete clinic
workflow. Server checkpoint and atomic revision tests are recorded separately by
the backend subtask. Arabic message-key parity passed; no native-speaker review
or Arabic accuracy claim is made.

## Backend and repository gates

- Recovery and contract pytest suites: **311 passed**, including 15 recovery
  cases. The actual in-memory LangGraph and HTTP routes verify checkpoint
  recovery, authorization, a delayed original POST racing a recovery GET,
  concurrent answers, pure side questions with unchanged question IDs, and old
  clients that omit the optional expected revision. These tests do not establish
  cross-process Postgres locking; the existing advisory lock is reused.
- The first complete Python run passed **1,961 tests**, including the 13 HAPI
  tests, and exposed 11 voice fixture failures caused by the newly required
  response revision. Those were corrected. The voice serializer also now omits
  unset optional request fields, preserving its exact existing request body.
- Final `make check`, with `PYTEST_ADDOPTS="-m 'not hapi'"`: Ruff, format,
  ESLint, physical-direction CSS check, Redocly, Pyright and TypeScript passed;
  **1,959 Python tests passed, 6 skipped, 13 deselected**; **103 TypeScript tests
  passed**. The six skips were one live-extraction test, three KTAS-data tests
  and two live-voice tests. No safety assertion was weakened.
- Database and event tests used dedicated throwaway Postgres on 55439 and Valkey
  on 56389 with `CAREFLOW_REQUIRE_DB=1` and `CAREFLOW_REQUIRE_REDIS=1`.
  `CAREFLOW_TEST_ADMIN_URL`, `DATABASE_URL`, `DATABASE_URL_MIGRATOR` and `REDIS_URL`
  were set for those instances; credentials are intentionally omitted here.
- HAPI's adapter reads its configured URL directly and ignores `FHIR_BASE_URL`.
  Consequently, the first run's 13 HAPI tests used the existing localhost:8080
  synthetic server. They passed; write tests use fresh session IDs and remove
  their own resources in cleanup. The final run excluded HAPI to avoid repeating
  those shared-service mutations. This was not an isolated whole-stack run.
- `make contracts-breaking`: no breaking changes. Regenerating contracts changed
  **0 of 27 generated files**. `git diff --check` passed.
- Independent source review found and then verified closure of the root-page
  expired-session trap and the called-patient waiting-instruction error.
- Production-build browser regression suites: kiosk **8 passed, 1 skipped**
  (real voice is opt-in), staff **14 passed**, companion **9 passed**. These
  31 passing checks use controlled API responses/Prism, not a real backend.
  All six test-app/mock ports were closed afterward. Regenerated historical
  screenshots were restored rather than replacing earlier milestone evidence.

All test APIs use synthetic identities. The actual local language model, real
speech engines and physical-phone connection were not exercised by this fix.

## Remaining boundary

A QR claim whose **successful response is lost before its new credential reaches
the browser** cannot be recovered by this client alone: the one-time claim API
has consumed the code, and the browser does not have the token. The UI permits a
request retry and reports an expired/rejected code honestly, but this change does
not provide server-side idempotency for QR claims. A fresh kiosk/reception
handover remains necessary in that case.
