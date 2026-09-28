# CareFlow product context

## Register

product

## Users

Patients checking in at a clinic kiosk under bright ambient light, often anxious or unfamiliar with the system; nurses reviewing a live queue; doctors reading sourced visit briefings; patients following the visit on a phone. All demonstrated patient data is synthetic.

## Product Purpose

Connect private patient check-in, structured intake, deterministic safety alerts, nurse-confirmed urgency, queue coordination and sourced doctor briefings. The interface should make the next action evident and preserve the implemented workflow. This is a research prototype, not a clinical deployment.

## Brand Personality

Northstar General Hospital is the example hospital identity. Use warm white, navy and cobalt with recognisable department icons. The owner explicitly rejected the earlier sage and teal direction; the attached HTML is reference material, not a requirement.

## Anti-references

Avoid generic piles of forms, oversized desktop controls, disconnected screens, decorative clinical metrics and unlabelled simulated status. Never disguise unavailable backend data with fabricated examples.

## Design Principles

1. The first screen offers “Tap to begin” or a phone QR before patient details.
2. Preserve nurse authority and make suggested versus confirmed urgency explicit.
3. Show where facts come from and report real connection and loading states.
4. Give kiosk, phone and staff their own density within one shared visual system.
5. Identify or register, select the actual patient, then describe the problem naturally. Department preference is optional. Structured answers do not need an LLM; natural descriptions use validated local extraction and the next relevant question.
6. Provision a clinic device before patient use. Staff/device authentication never interrupts a patient visit. Returning patients can find and verify a demo record by ID or mobile; new patients register directly. A child or dependent has a separate identity and history from their caregiver.
7. Back preserves unsent answers. Guided demo sensors show time, generated readings and automatic progression only after a fresh reading is saved. Touch ID is not a medical sensor.
8. Demo doctor availability is labelled; choosing a doctor is a preference, not an appointment booking.

## Accessibility and Inclusion

English and Arabic with logical layout properties and right-to-left support. Preserve visible focus, adequate contrast, touch targets, labelled controls and reduced motion. Arabic is machine-checked only, not validated by native speakers. These requirements derive from README.md and docs/CONVENTIONS.md.

## Interaction and AI boundaries

Hold the microphone button or Space to speak; release to stop capture and submit. Consented camera preview is local and optional, with no clinical inference. The patient receipt shows sourced information and a provisional model result; the nurse confirms the final urgency. Rule identifiers, visit references and queue tokens have separate labels. Urgent-help alerts must never wait for sensor completion.
