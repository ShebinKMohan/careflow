# Adaptive intake UI browser evidence

This is **fixture-backed frontend evidence**, captured against the local Next.js dev server on 28 September 2026 UTC (29 September local time). It does not demonstrate live extraction, model quality, real clinical prioritisation, backend persistence, or human acknowledgement. The screenshots deliberately label the urgency model `laya-ktas-demo` and its version `fixture-only-not-live-model`.

## Verified browser matrix

| Language | Viewport | Intake + receipt axe violations | Camera start/stop | Navigation cleanup | Horizontal overflow |
|---|---|---:|---|---|---|
| English | 1440 × 1000 | 0 + 0 | Passed | Passed | None |
| English | 1280 × 800 | 0 + 0 | Passed | Passed | None |
| English | 390 × 844 | 0 + 0 | Passed | Passed | None |
| Arabic | 1280 × 800 | 0 + 0 | Passed | Passed | None |
| Arabic | 390 × 844 | 0 + 0 | Passed | Passed | None |

All five browser scenarios passed after the final simplified-layout changes. All ten axe scans passed the tested WCAG 2.0/2.1 A and AA checks. This is automated coverage of the captured screens, not a complete accessibility certification or native-speaker validation. No JavaScript page errors or unexpected API requests occurred.

The test traverses new-patient registration, consent, the adaptive-intake screen, an editable suggested answer, a typed fixture turn, the pain scale, optional vitals bypass, and the sourced patient receipt. It checks Arabic document direction and keeps the visit reference left-to-right.

## Camera proof boundary

Chrome was launched with `--use-fake-device-for-media-stream` and `--use-fake-ui-for-media-stream`. The real browser `getUserMedia` and video rendering pipeline ran with its synthetic `fake_device_0` video source. No real room or person was captured. Browser permission approval was simulated by the browser launch flag; this does not prove interaction with a human-operated permission prompt.

The test confirms:

- No camera track exists before the user enables the preview, even after consent.
- Keyboard Enter starts the preview; the synthetic video track is live and the video has non-zero dimensions.
- Stop changes the track to `ended` and clears the video element's `srcObject`.
- A second preview is automatically stopped when navigation leaves intake for vitals.

## Visual findings and changes verified

- The conversation now has a single question heading, a compact assistant mark and Back control, and a prominent round microphone action. Repeated introduction and answer-instruction paragraphs were removed.
- One visibly labelled composer contains the editable answer and an accessible Send icon. Its label, field, and action are visible within the initial desktop and 390-pixel phone viewports.
- Suggested starters are small pills explicitly marked “Example”; selecting one inserts text and focuses the real answer field. They are visually distinct from that field.
- Camera and phone handoff are compact utilities. The camera has no empty preview panel while off; its video is shown only when enabled. Privacy details remain available in disclosures.
- The earlier oversized Back icon and two receipt contrast failures are fixed.
- Receipt icons, long rule codes, provenance labels, missing measurements, and left-to-right visit references render without horizontal overflow in English and Arabic.

## Files

Each `<language>-<viewport>` prefix includes `intake.png`, `camera-on.png`, `pain-scale.png`, `receipt.png`, and a detailed `report.json`. `summary.json` aggregates the results.

Reproduce from the repository root with the kiosk dev server on port 3000:

```sh
E2E_SERVER=dev pnpm --filter kiosk exec playwright test tests/e2e/adaptive-ui.spec.ts --project=chrome --workers=1
```

The test reuses the monorepo's installed axe Playwright library from the staff workspace. All `/v1/` HTTP responses used by these scenarios are intercepted fixture responses. No model is invoked by this suite.
