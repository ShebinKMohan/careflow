# ADR-048: Recoverable QR claims, renewable phone sessions and a reachable phone origin

Status: accepted, 27 September 2026 (SHE-300 [M2-06]; builds on ADR-037 and ADR-044)

## Context

The QR hand-over (ADR-037) and the phone's reload recovery (ADR-044) left four gaps:

- **A lost answer to a successful claim stranded the patient.** The claim spends the code, marks
  the session handed over and tells the kiosk, which forgets the patient. If the phone never got
  the answer, its retry was "already used" (409), the kiosk could not issue a new code (409), and
  the patient had to start again at the desk. ADR-044 named this as open.
- **Phone tokens lasted 30 minutes, with no renewal.** A patient waiting longer lost the waiting
  room and "I feel worse" (M2-07), the phone's safety feature.
- **The kiosk only listened while its QR panel was open.** A code shown, then "Stay on the kiosk",
  then scanned, handed the session over without the kiosk noticing. Its patient token was then
  refused (401), but the kiosk kept the patient on screen.
- **A real phone could not reach the stack.** The QR link and the phone's API address were
  `localhost`, which on a phone is the phone.

The journeys also found that the hand-over mark in Redis lasts 2 hours: once it expired, another
code of the same session could be claimed a second time.

## Decision

- **The claiming phone may ask again, nobody else.** The phone makes a random `claim_key` (32
  bytes, base64url) for each code, keeps it in memory with the code and sends both in the claim.
  The claim keeps only the key's SHA-256 with the spent code. A spent code sent again with a key
  whose hash matches, while the code is still valid (120 s plus 10 s of leeway), is that phone
  asking again: every step after the spend is safe to repeat, so it gets a new phone token for the
  same session and the kiosk gets the frame again. Without the key, with another key, or after
  the code runs out, a spent code is still 409 (or 401). The hand-over mark records which code
  handed the session over, so a retry of that code passes it and any other code does not. The key
  never touches storage or a URL; a phone reloaded before the retry loses it and asks at the desk.
- **The graph remembers the hand-over.** A code is refused (issue and claim) when the graph's
  `channel` is already `phone`, which outlives the 2-hour Redis mark.
- **Renewal, bounded.** `POST /v1/sessions/{id}/renew` swaps a still-valid phone-channel token of
  that session for a new 30-minute one while the visit is open (not closed or a no-show) and at
  most 12 hours after check-in. A kiosk-channel token or a device gets 403, an expired or revoked
  token 401, so renewal never revives the kiosk's credential or an expired one. The old token
  stays valid until it runs out (there is no per-token revocation, ADR-037). The phone renews 10
  minutes before expiry, and at once when it wakes or comes back online near that; a phone asleep
  past its token's 30 minutes asks at the desk. No rate limit: the route needs a valid phone token,
  and a clinic's phones may share one address.
- **The kiosk forgets a revoked patient.** It listens for `session.handed_over` from the first code
  on, not only while the panel is open, and any 401 on the patient's calls (the hand-over it missed,
  or a token that ran out) wipes the patient and returns to the welcome screen. A late 401 for an
  earlier patient's token changes nothing.
- **A reachable origin, opt-in.** `CAREFLOW_PUBLIC_HOST=<this computer's LAN address> make up`
  puts that address in the QR link and in the apps' API URL, adds its origins to CORS, lets the
  Next dev servers accept it and makes the API listen on every interface. Without it everything
  stays on localhost. Plain HTTP on the LAN is a demo setting: the service worker and install
  prompt need HTTPS, and the claim itself does not.

## Consequences

- A claim whose answer is lost is recovered by the same phone within the code's lifetime; a
  second phone that saw the code still gets nothing.
- A phone session lasts as long as the visit, up to 12 hours, while the phone is used at least
  every 30 minutes.
- The kiosk no longer keeps a handed-over patient on screen, however the hand-over reached it.
- Binding the API to the LAN may trigger the operating system's firewall prompt on the demo
  machine; that is the owner's decision, and the default never does it.
