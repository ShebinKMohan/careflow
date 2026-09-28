# ADR-056: Consent and the ID tap, verified

## Status

Accepted, 28 Sep 2026 ([M2-05], the test phase of ADR-036). Evidence: `docs/evidence/M2-05.md`.

## Context

ADR-036 built the consent gate and the mock ID tap, and left their tests for later. The Claude Code handoff of 27 September asked for more than those tests:
- camera and microphone access that follows the *saved* consent, through refusal, revocation, a reload and a failed save;
- no path on which the kiosk carries on as if a failed save had worked;
- both check-in paths run against the real isolated FHIR adapter;
- a scan of the captured logs for identifiers, tokens and patient text;
- a record of where the implementation ends and the simulated hardware begins.

Writing those tests found six defects:

1. **A failed save was ignored.** The kiosk posted the consent without looking at the answer. It went on to the intake whatever happened, and `hasConsent` read the unsaved choices. An API error or a dropped network left a patient who had agreed to voice with the microphone button showing. The API and the voice service had no record for that session.
2. **Voice ignored data use.** The kiosk required data use for the microphone. The API's voice-turn check and the voice service read `voice` only. A record with `voice: true, data_use: false` opened the microphone on the servers.
3. **Card numbers were in URLs.** The FHIR client searched with `GET Patient?identifier=<system>|<id>`, so the synthetic card number sat in the request URL. httpx logs that URL at INFO, and so would HAPI's access log and any proxy on the way. The contract says the number goes "only ever in the body, never in a URL or a log".
4. **Check-in hung when the API was unreachable.** `api.POST` rejected, the rejection went unhandled, and the check-in screen stayed on "Checking in…" until the idle wipe.
5. **The voice service could not start under `make up`** on Apple silicon (below). The voice journey needs it.
6. **A second stack's voice service called the dev stack's API.** The stack env names the API `CAREFLOW_API_URL`, but the voice service reads `CAREFLOW_API_BASE_URL`, which defaults to 127.0.0.1:8000. On the e2e stack its consent check failed closed, as it should, and every spoken answer would have gone to whatever answered on 8000.

## Decision

### The choices count only once the API saved them

- **In the kiosk.** `setConsentChoices` holds the choices unsaved. `markConsentSaved()` is called only when `POST /v1/sessions/{id}/consent` returns 2xx. `hasConsent` is false until then, and again after any new choices.
- **When the save fails.** An error answer or no answer at all never carries on to the intake. The kiosk says "Your choices were not saved. Please try again, or ask at reception." (`safety.consent.saveFailed`) and offers:
  - **Try again** (`safety.consent.retry`), which posts the same choices for the same session;
  - **Start again**, which wipes the patient and goes home, not back to the consent screen.
- **The orphaned session.** Check-in has already opened the session, because the consent route needs one. After "Start again" that session holds no consent and no answers. It is abandoned like any walked-away session and expires.

### The microphone needs voice and data use, everywhere

`allows_voice(record)` in `careflow_api.consent` is `voice and data_use`. It gates:
- the API's voice turns;
- the voice service's `voice_consented` (1008 otherwise);
- the kiosk's `hasConsent("voice")`.

Declining data use never reaches check-in (ADR-036). The servers still refuse a record that says so.

### FHIR searches send their parameters in the body

`FhirClient.search` uses `POST [type]/_search`, with the parameters form-encoded (FHIR R4 RESTful search). No patient identifier (card number, keypad id) is ever in a URL.
- **Paging.** HAPI's next links carry only `_getpages`, and they are followed with GET as before.
- **Retries.** A search is a read, so it is retried as one.
- **Checks.** The real-HAPI integration tests pass on `POST _search`.
- **Tests.** `test_a_search_never_puts_its_parameters_in_the_url` checks both the URL and httpx's log line.

### The kiosk says when check-in cannot reach the API

A rejected keypad or card request shows "The kiosk cannot reach the clinic system right now…" (`checkin.unreachable`) and re-enables the screen. It is not counted as a failed attempt, so a network drop never sends a patient to reception as if they had guessed wrong.

### `make up` starts the voice service again

Since M2-08 (#34), `scripts/up.sh` has passed its sync extras to the voice process too. The `embeddings` extra belongs to careflow-rag, so `uv run --package careflow-voice --extra embeddings` refused to start ("Extra `embeddings` is not defined"). honcho then stopped the whole stack. Every `make up` on Apple silicon with the voice models downloaded failed this way; the dev stack ran with `VOICE=0`, so nobody saw it. The voice process now takes only its own extras (`mlx-whisper`, `arabic-speech`), and the sync keeps all three.

### Each stack's voice service calls its own API

`scripts/stack.py` writes `CAREFLOW_API_BASE_URL=http://127.0.0.1:<the stack's API port>` beside `CAREFLOW_VOICE_URL`. The two services of one stack now find each other at any offset. `scripts/tests/test_stack.py` pins both.

### The mic button follows the voice service

When `make up` runs the voice service, it prefixes the kiosk's command with `NEXT_PUBLIC_VOICE_ENABLED=${NEXT_PUBLIC_VOICE_ENABLED:-true}`. honcho's shell expands it after `.env` is loaded, so an empty value counts as unset and an explicit `false` still hides the button. Without a voice service nothing changes. The button still shows only after the saved voice consent.

### How it is tested

- **The API** (`services/api/tests/test_consent_and_id_tap.py`, real app, Postgres, Valkey and HAPI):
  - storage: append-only, enforced by the app role's grants, and the latest record wins;
  - the event holds booleans only;
  - voice turns follow the latest record;
  - own-session and role isolation;
  - the ID tap with one, no and two matches (two synthetic duplicates made and deleted in HAPI);
  - the kiosk and clinic boundaries;
  - every test's logs scanned at INFO, and DEBUG for CareFlow's loggers. SQLAlchemy's SQL echo stays at WARNING: it prints statement parameters by design and is a debugging switch.
- **The voice service:** 1008 for every refusal, and consent read again at every start.
- **The kiosk:** unit tests for saved-only consent, a failed save, retry and start again, and an unreachable API at check-in.
- **Journeys on the real stack:**
  - (b) no camera or voice: keypad, a full tap and text intake, 0 `getUserMedia`;
  - (c) no data use: no check-in or session request, no new Encounter;
  - (d) a dropped consent save, then a retry, with the ID card;
  - (a) agree, ID card, a spoken answer;
  - a reload forgets the patient and the consent.
- **How (a) listens.** Chrome's fake microphone plays the committed synthetic Kokoro recording. The real voice service and its local Whisper transcribe it. The API's extraction reply for that transcript is recorded like every e2e reply, by `e2e/record_voice_turn.py`. It posts the voice service's own request with the model up, because the model and the voice models are never loaded together.
- **Voice in CI.** (a) and the reload journey need a voice service. CI runs `VOICE=0`, so they are skipped there with that reason.

### Implementation and simulated hardware

Everything is the running implementation except two things:
- **The card reader.** It is the dev panel "Simulated ID reader" (`MockCardReader`), and every card number is synthetic.
- **The microphone in the journeys.** It is Chrome's fake capture device playing a synthetic recording.

No real ID reader, Emirates ID or UAE Pass service, and no real voice, is used anywhere.

## Consequences

- A patient who agreed is never treated as consented until the clinic system holds the record, and the servers and the kiosk agree on what the record allows.
- **One consent server-side gates only voice.** Typed and tapped answers need no voice consent. The kiosk reaches the intake only with a saved record, but the API does not refuse text turns for a session without one. Data-use refusal is enforced by never checking in.
- **An abandoned session.** A failed save followed by "Start again" leaves a session with no consent and no answers until it expires.
- **FHIR searches are POSTs.** Another FHIR server must support `POST [type]/_search`, which FHIR R4 requires.
