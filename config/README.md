# Config

`providers.yaml`, `rooms.yaml`, `intake.yaml`, `triage_scales.yaml` and `hospitals/` arrive with the issues that use them (see `docs/CONVENTIONS.md`).

- `rooms.yaml` (M1-10): queue departments, rooms and the levels each room accepts, the grace window, mean service minutes and the level-to-department routing. The API validates it at start.
- `rag.yaml` (M2-09): the handbook retrieval mode, `k` and embedder, and the ask-the-clinic agent's "I don't know" threshold (`min_score`), model timeout and remembered turns. The mode and threshold are provisional until the M2-08 retrieval eval.
