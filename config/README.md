# Config

`providers.yaml`, `rooms.yaml`, `intake.yaml`, `triage_scales.yaml` and `hospitals/` arrive with the issues that use them (see `docs/CONVENTIONS.md`).

- `rooms.yaml` (M1-10): queue departments, rooms and the levels each room accepts, the grace window, mean service minutes and the level-to-department routing. The API validates it at start.
