# ADR-050: The Arabic UI, checked by machine and verified end to end

Status: accepted, 27 September 2026 (SHE-273 [M2-01]; completes ADR-032)

## Context

ADR-032 built the Arabic right-to-left UI and left its gates for later:
- the back-translation drift check and its `--check` gate;
- `safety.` keys in the staff app;
- the `@rtl` screenshot tests;
- a check that Arabic survives the paths a patient takes (a kiosk refresh, the QR claim, a phone
  refresh, a lost connection, the end of the visit).

There is no native Arabic reviewer, so every check is a machine check, and it is described as one.

## Decision

### The back-translation gate (`scripts/i18n/backtranslate.py`)

- **Two model passes, then chrF.** The `translation_check` provider (the local Qwen3.5-9B) first
  translates each Arabic string back to English without seeing the English, so it cannot copy it.
  A second pass judges whether the back-translation means the same as the English original. chrF
  (sacrebleu 2.6.0, pinned) then scores the pair. A string is flagged when the judgement says the
  meaning differs, or when chrF < 40. The floor is the issue's and is never loosened.
- **What it covers.** The strings of the three apps, and the shared footer. The footer's strings
  moved into `packages/ui/src/messages/*.json`, because "Not for clinical use" must be checked too.
  The first run found its Arabic "نموذج بحثي" read back as "search form".
- **Only what changed is checked again.** The report keeps each row's model and time, and the
  SHA-256 of every `ar.json` and `en.json`. A run re-checks new keys and changed strings (English
  or Arabic), and a checkpoint lets an interrupted run resume. A batch answered with missing items
  is asked again in halves.
- **`--check` is offline and runs in `make lint`.** It fails when:
  - a messages file changed since the report;
  - a flagged `safety.` key is not listed in `docs/i18n/unresolved.md`;
  - a listed key does not show its English original beside the Arabic, or is not a key at all.
- **The five-attempt rule.** A flagged `safety.` string is rewritten in simpler Modern Standard
  Arabic up to five times. If it is still flagged, it keeps its best Arabic with the English
  original in brackets, and it is listed with every attempt. The five that remain follow up in
  SHE-324 (native review, needs-owner).

### Safety keys in the staff app

The nurse's safety-critical strings now live under `safety.`:
- the alert banners and ECG countdown (`safety.alerts`);
- the patient's "I feel worse" presses (`safety.feelsWorse`);
- the medical questions asked on the phone (`safety.medicalQuestions`);
- the triage card's "Suggestion only. You decide." and its red warnings (`safety.triage`).

### Screenshots from one pinned browser

The `@rtl` tests compare screenshots taken by the browser inside
`mcr.microsoft.com/playwright:v1.63.0-noble`, the repo's Playwright version.
- `make rtl` (`scripts/playwright_image.sh`) runs that image's `playwright run-server`. The test
  runner, the apps and Prism stay on this machine, and `PW_TEST_CONNECT_EXPOSE_NETWORK=<loopback>`
  lets the browser reach them.
- Baselines have no platform suffix. Outside the image, `ignoreSnapshots` skips only the
  screenshot comparisons; every other assertion runs.
- A test freezes the clock at a fixed moment before each screenshot. The phone's screenshots are
  of its screen, not the full page, because the sticky "I feel worse" button would land mid-page.

### Mixed direction and the patient's path

- **Latin text in Arabic sentences is isolated.** Patient initials now join tokens, rooms and ids
  inside `<ltr>` (rendered `<bdi dir="ltr">`) in every sentence. "M. S." rendered as ".M. S"
  before. The kiosk keypad stays left to right, like a phone's.
- **The phone switches to the session's language with one full load, not `router.refresh()`.**
  The claim takes its one-time code out of the address bar before Next's router takes over
  history, so the refresh put the code back, and any later refresh of an Arabic phone claimed a
  used code ("this code didn't work"). The full load happens only once the cookie is set, so it
  cannot loop.
- **The end of the visit reaches the phone.** The queue's broadcast covers only waiting and
  called tokens, so a seen patient's phone kept saying "go to room ...". After a nurse marks a
  token arrived, a no-show or seen, the API now sends that token's own phone its state. The phone
  shows "This visit has ended. If you feel worse, tell the reception desk now." or the no-show
  message, in the session's language.

### Found by the Arabic journey, fixed here

- **A visit finished on the phone reached the EHR.** Such a patient skips the kiosk's vitals, but
  the rules keep a partial NEWS2 with every input missing. The write-back built a NEWS2
  Observation derived from nothing. It had no provenance, so safety rule 4 refused the whole
  Bundle, and the nurse's level never reached HAPI. The nine `triage.confirmed` events of the
  M2-07 journey runs sat dead-lettered. The builder now leaves out a NEWS2 with no measured input.
- **Journey tooling.**
  - `E2E_RECORD=add` keeps the recorded replies and adds a new journey's.
  - The extractor's timeout is a setting: 20 s in production, 90 s in the E2E config, which is
    only reached while recording with the local model.
  - The journeys' HAPI searches send `Cache-Control: no-cache`, because HAPI reuses a search's
    results for 60 s.

## Consequences

- The report checks 453 strings, 91 of them `safety.`. After the rewrites none of the `safety.`
  strings is flagged except the five listed, which show their English. 113 other strings stay
  flagged in the report; they are not gated. Most differ in wording only ("Start over" for
  "Start again"). The ones that read back with another meaning were rewritten (for example
  "See my place in the queue" read as "Show floor plan", "missing" as "Low", "Other" as "Last").
- Adding or editing a UI string now needs `scripts/with_llm.sh uv run python
  scripts/i18n/backtranslate.py` before `make lint` passes. That run re-checks only the change,
  in seconds.
- The screenshots need Docker and the 3.5 GB image. CI does not run the apps' mocked Playwright
  suites (M0-08), so it does not run the screenshots either; they run with `make rtl`. The Arabic
  journey runs in CI's e2e job, replayed.
- Fact names and values on the staff screens stay English-coded in Arabic, as the rules read them;
  the patient's own Arabic words keep their direction (`dir="auto"`).
- No Arabic accuracy figure exists anywhere, and none of this is a native review.
