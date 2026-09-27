# Handbook review (M2-08)

Every handbook file passes an automated review before it is ingested. The owner may spot-check;
that never blocks.

## Checklist (per file)

1. The same section ids, in the same order, in `en/` and `ar/`.
2. Fictional only: "CareFlow Demo Clinic", "Insurer A/B/C"; no real hospital, insurer or person
   (the denylist test).
3. Practical information only: no medical advice, no diagnosis, no dosing.
4. The Arabic passes the M2-01 back-translation check (`same_meaning` true, chrF at least 40).
5. An `eval_judge` pass, by a different model from the writer, confirms items 2 to 4.

## Log

Not run yet: the automated review (back-translation, denylist, judge) is part of the M2-08 test
phase. Written by `agent:claude-code/claude-opus-5-5` from `OUTLINE.md`; the Arabic is not
validated by native speakers.
