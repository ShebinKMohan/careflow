# CareFlow interface rebuild — frontend verification

Verified locally on 28 September 2026. The screens remain a research prototype using synthetic data. These results establish the tested interaction and security boundaries; they do not establish clinical effectiveness, production deployment readiness or native-speaker validation of Arabic.

## Browser regression results

All three applications successfully built for production during their browser checks. The application suites used Chrome and controlled contract-shaped API responses or Prism examples. They ran on ports 3100–3102 alongside the live development applications on 3000–3002.

| Application | Passing scenarios | Coverage |
| --- | ---: | --- |
| Kiosk | 10 | New kiosk entry action; English and Arabic check-in, consent, identity, intake, vitals and final token; idle reset; failed ID recovery; touch targets |
| Staff | 23 | Nurse decisions and overrides, NEWS2 save, ECG timing and completion, live-event fallback, doctor provenance, public-board privacy, system status, Arabic direction, search safety and responsive layout |
| Companion | 12 | Phone-first permission and identity through intake, queue and nurse-alert dialog; prior QR handover; network recovery; session reload; English/Arabic phone layouts; PWA manifest and API-cache exclusion |

The kiosk result consists of nine passing scenarios in the complete final suite plus a successful targeted rerun of the Arabic smoke scenario after its mock installation setup was updated. The full suite's initial failure was a disabled start button on an unconfigured mock kiosk. No application guard was removed to make this pass.

The staff worklist alert test now fixes the clinical clock while measuring delivery latency with the real test-runner clock. This removes an existing one-second countdown race without weakening the one-second alert-delivery requirement. The final complete staff run observed the alert with evidence in 58 ms against its controlled WebSocket.

Additional browser assertions confirm:

- Filtering the patient table cannot hide its safety-alert banners.
- The Arabic staff worklist fits a 390-pixel viewport without whole-page horizontal scrolling.
- The phone-first flow does not submit identity until explicit consent is given, removes the entry token from the URL fragment, and does not save the identity digits or entry ticket in session storage.
- The nurse-alert dialog receives focus, traps forward and backward Tab navigation, closes with Escape, and returns focus to its trigger.
- Core kiosk answer/keypad touch targets measured 64–66 pixels in the completed journey.
- Axe reported no serious or critical violations on the nurse worklist, doctor briefing, public queue board and system-status screens checked by these suites.

Local Playwright screenshot comparisons were disabled by the existing configuration because the pinned screenshot-comparison container was not used. Direction, interaction, accessibility and layout assertions still ran. The captured PNGs are visual evidence, not a claim that the historical pixel baselines match the redesign.

## Kiosk proxy and HTTP transport

The final kiosk unit run passed **68 tests in 13 files**, including the explicit staff setup regression tests. Kiosk ESLint and TypeScript checks passed. The independent additions include:

- **15 server-proxy tests** covering endpoint allowlisting, rejected foreign origins, rejected cross-site requests, the 8 KiB body limit, unavailable provisioning, bootstrap credential omission and private-header omission.
- Session-specific proxy requests require a patient credential. Invalid or different-session credentials cannot trigger device operations. The vitals path verifies the exact session, patient role and matching clinic before it uses the server-held device credential.
- A patient status read uses the patient's scope without using the device credential.
- **Two actual local HTTP/1 transport tests** confirm that the server-managed client sends bounded JSON with `Content-Length`, does not send chunked streaming data, preserves the JSON payload and does not expose the device authorization header to the browser-facing proxy. Direct legacy-device API calls retain their device authorization while omitting the BFF-only patient-proof header, which avoids an unsupported CORS preflight header.

The transport test uses an ephemeral local HTTP server; the security tests use controlled upstream responses. Live API/DB/model results are recorded separately in [the runtime verification report](../2026-09-28-ui-rebuild-runtime.md).

## Screenshots

The following `live` images were taken from the running local applications. A fresh kiosk browser obtained its QR ticket from the real API; opening that same ticket in the companion reached permission without a staff login. No whole-page horizontal overflow was observed on the 1280-pixel kiosk or the 390-pixel companion home screen. This check did not perform an optical scan with a physical phone camera or complete an actual patient's check-in.

- Kiosk welcome and real entry QR, 1280-pixel viewport: captured locally; the image is excluded from Git because the QR contains a signed entry capability.
- [Companion home, 390-pixel viewport](screenshots/companion-home-phone-live.png)
- [Companion permission reached using the live kiosk ticket](screenshots/companion-permission-phone-live.png)

The following screenshots use controlled synthetic API fixtures and are explicitly named accordingly:

- [Nurse worklist, 1280-pixel viewport](screenshots/staff-nurse-desktop-fixture.png)
- [Arabic nurse worklist, 390-pixel viewport](screenshots/staff-nurse-arabic-phone-fixture.png)
- [Phone intake](screenshots/companion-intake-phone-fixture.png)
- [Phone queue](screenshots/companion-queue-phone-fixture.png)
- [Phone nurse-alert dialog](screenshots/companion-nurse-alert-phone-fixture.png)

Screenshots use full-page capture, so their saved height can exceed the original 800- or 844-pixel viewport. The local development images may show Next.js's development indicator. Existing suites also generated legacy-path screenshots; fresh copies were moved into `screenshots/regression-fixtures/`, and the original historical tracked PNGs were restored.

## Reproduction

```sh
E2E_PORT=3100 NEXT_PUBLIC_VOICE_ENABLED=false pnpm --filter kiosk test:e2e --project=chrome
E2E_PORT=3101 pnpm --filter staff test:e2e
E2E_PORT=3102 pnpm --filter companion test:e2e
pnpm --filter kiosk test
pnpm --filter kiosk lint
pnpm --filter kiosk typecheck
```

The controlled kiosk suite intentionally disables voice; real voice/model execution is outside these browser-mock results and must be assessed using the separate runtime evidence.

## Shared system and reproducible startup

Shared UI tests: **24 passed**, including contrast and logical RTL utilities; shared lint and
TypeScript passed. Staff unit tests: **53 passed**; companion unit tests: **46 passed**, with
lint and TypeScript passing in both apps. The local launcher suite passed **30 tests** after
persisting default synthetic-clinic provisioning and LAN API routing through the companion
port, including offset stacks and an explicit provisioning opt-out. Ruff passed for these
launcher changes. The user's existing intake configuration was preserved.

The runtime report records separate complete live passes for both phone-first and kiosk
journeys; these used actual local Qwen, API, Postgres, Valkey and HAPI. No production or
clinical-readiness claim is made. See ADR-059 for the entry and credential-boundary decisions.
