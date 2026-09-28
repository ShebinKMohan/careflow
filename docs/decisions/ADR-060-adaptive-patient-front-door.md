# ADR-060: Identity-first adaptive patient check-in

Status: Accepted for the synthetic demonstration, 29 September 2026.

## Context

The owner requested an identity-first flow with returning records, separate caregiver identity, natural English/Arabic intake, hold-to-talk, a camera preview, actual Laya urgency suggestions, and an actionable urgent-help path. This replaces mandatory department/doctor selection in the primary patient journey. The preceding fast registration path remains an explicit alternative API, not a substitute for clinical intake.

## Decision

- The welcome screen says “Tap to begin” and offers the existing expiring QR entry link.
- Record lookup is demo-only, scoped to the provisioned device or QR claim. A masked match plus birth-year verification precedes disclosure. This is not production identity assurance. A new patient can register without an account; a caregiver identity never supplies the dependent's clinical history.
- The primary flow invokes the existing adaptive graph. Explicit yes/no and scale answers retain deterministic parsing; natural descriptions use the local validated extractor. Approved question templates remain enabled, so a second language-model call is unnecessary to phrase each question.
- The local urgency backend is explicitly selectable. Laya is only named when its actual fine-tuned artifact loads; selected-model failure yields no suggestion rather than a mislabeled silent fallback. Clinical rules retain priority and every final urgency still requires nurse confirmation.
- A patient may inspect an own-session receipt with sourced facts, safety-rule identifiers and an explicitly provisional model suggestion. This targeted change follows the owner's request to show the result of the assessment. It is not a diagnosis, medical advice, or authorization to wait. The existing general status route stays free of clinical fields; staff-only facts and triage routes are not broadened.
- Rule identifiers such as FAST_STROKE are not hospital emergency announcements or billing/diagnosis codes. A visit reference is not a queue token; queue assignment follows nurse review.
- Urgent help creates an anonymous, idempotent help request and publishes the nurse alert immediately after the patient's data-use choice. Optional symptom selections run through existing safety rules. Adults can continue to the simulated health check or skip it; children go to nurse observation. Neither identification nor a vital reading delays the initial alert. The reference stays attached to the request through nurse confirmation and queue assignment.
- Microphone capture happens only while the control/Space is held. Release stops tracks and submits the utterance; playback does not restart capture. Camera is a separately activated, consented local preview: no frame storage, transmission, or clinical inference.
- Adult vitals remain a guided, visibly simulated device sequence. The paediatric branch hands observations to a nurse rather than treating the adult cradle or adult urgency model as appropriate for a child.
- A child without a supplied complaint receives one opening question before the mandatory paediatric safety bank. The ordinary conversation cap cannot discard the remaining child safety screens. Existing complaints and previously answered safety questions are preserved.

## Consequences

The demonstration now exposes the actual model and data boundaries instead of inferring capability from a pitch document. No camera distress detector, cough classifier, production hospital authentication, regulatory certification, clinical accuracy threshold or physical sensor measurement is established by this change. Local HTTP on a phone can support check-in, but browser microphone capture requires a secure origin; localhost supports the Mac voice demonstration.

Verification and measured performance are recorded in `docs/evidence/ADAPTIVE-20260929/` after live execution; source code or mocked tests alone do not establish end-to-end success.
