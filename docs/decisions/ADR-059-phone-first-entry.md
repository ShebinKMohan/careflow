# ADR-059: Phone-first clinic entry and server-managed kiosk setup

Status: Accepted, 28 September 2026.

## Context

The old entry screen asked for language first, offered phone handover only after a visit
already existed, and exposed device setup midway through an unconfigured patient's flow.
The owner requested an immediate choice of phone or kiosk, with no staff/device login
interrupting patient check-in.

## Decision

- Display kiosk check-in and a real phone-entry QR on the welcome screen. Language is a
  header setting. Consent and six-digit patient identification remain explicit steps.
- Separate reusable, expiring clinic-entry tickets from existing single-use patient handover
  tickets. Entry tickets contain no patient data or device credential. A phone's random
  claimant key and stable session reservation make uncertain-response retries idempotent.
- Keep installation credentials on the kiosk server. Browser setup receives a noncredential
  marker. A narrow same-origin proxy permits only clinic entry and the necessary vitals
  operations. Session operations require verified patient authority; device vitals additionally
  require a matching clinic and patient role.
- Enable server provisioning in the seeded local launcher, with an explicit opt-out. Preserve
  the separate staff installation route. Unavailable stations show recovery/reception guidance
  before allowing the patient to proceed.
- For an explicitly configured LAN host, route browser API traffic through the companion
  Next.js reverse proxy. The API still performs authorization. Local-only and mock modes
  retain their existing direct URLs.

## Consequences and evidence

The phone and kiosk paths remain distinct through intake and converge on nurse-confirmed
triage, queue assignment and sourced briefing. Child details and consent are recorded before
phone intake. Patient identifiers are identification within this synthetic prototype, not
production-grade identity verification.

Both paths passed live local-model journeys through HAPI write-back. Security, transport,
responsive and fixture-browser evidence is recorded in
`docs/evidence/UI-REBUILD-20260928/README.md` and
`docs/evidence/2026-09-28-ui-rebuild-runtime.md`. A physical handset scan, clinical validation,
and production deployment are not established by those checks.
