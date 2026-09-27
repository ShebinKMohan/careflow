# ADR-032: Arabic right-to-left UI and Western digits

## Status

Accepted, 27 Sep 2026 ([M2-01]). Core built first by the owner's instruction; the back-translation checker and the `@rtl` Playwright tests follow.

## Context

Many patients at a Gulf clinic read Arabic first. The three apps already switch locale by cookie (M0-08), set `lang` and `dir` on `<html>`, and ship matching `en.json` and `ar.json` key sets. The M2-01 gaps were:

- the kiosk never told the API which language the patient chose, so every session was created in English;
- the language could still change mid-session;
- tokens and ids inside Arabic sentences had no bidirectional isolation;
- there was no guard against physical-direction CSS;
- the footer did not say that there is no native-speaker review.

## Decision

- **Language per session.**
  - `KeypadCheckinRequest` gains an optional `language` (additive; English when absent). The kiosk sends the language picked on its first screen, and the API creates the session and starts the intake in it.
  - The kiosk shows its language toggle only on the first screen and on the staff-only device setup. From check-in on, the session keeps its language.
- **Font.** IBM Plex Sans Arabic (weights 400, 500 and 600) replaces Noto Sans Arabic in all three apps. The token stack puts it first for `lang="ar"`.
- **Bidirectional isolation.** A token, room or session id inside a translated sentence is a rich-text `<ltr>` tag rendered as `<bdi dir="ltr">`. Tokens that stand alone already sit in `dir="ltr"` blocks.
- **Western digits in both languages.** Token numbers must read the same on the kiosk, the phone and the queue board.
  - Current ICU and CLDR already format generic `ar` with Latin digits. `ar-SA` would give Arabic-Indic.
  - Every date and time format also pins `numberingSystem: "latn"`, so a different runtime cannot switch them.
- **No physical-direction classes.** `scripts/check-physical-css.mjs` fails on `ml-/mr-/pl-/pr-`, `left-/right-`, `text-left/right`, `border-l/r`, `rounded-l/r` (and their corner forms) and `float/clear-left/right` in the apps and the shared UI. `make lint` runs it. The apps had no hits: M0-08 already used logical classes.
- **The footer** carries both disclaimers in both languages:
  - "Research prototype. Synthetic data only. Not for clinical use."
  - "Arabic supported; not validated by native speakers."
- **No Arabic accuracy percentage** anywhere; the Arabic strings stay machine-written and machine-checked.

## Consequences

- **The Arabic intake itself is M2-03.** Until it lands, a patient who picks Arabic gets the Arabic UI with the English checklist questions.
- **Still to come:**
  - the `safety.` key prefix and the back-translation drift checker (`scripts/i18n/backtranslate.py`, its `--check` gate and the report);
  - the per-app key-parity Vitest tests;
  - the `@rtl` Playwright snapshots.
