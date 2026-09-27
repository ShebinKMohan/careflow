# hgb 1.0.0

Status: released. Registered 2026-09-27T07:53:15+00:00. Research prototype; not for clinical use. The model only suggests a level: the rules run first and a nurse confirms every level.

- Artifact: `services/triage/artifacts/ktas-hgb-ebf31b5e-s42` (git-ignored), manifest sha256 `6684a3a5ec13a0970784fd0ef65b52ed32486b5b9cdfd13872cb4f21d1d05eec`, 3 files, 2 MB.
- Data: KTAS (PLOS ONE 2019, CC BY 4.0), split `ktas_v1.json`, CSV sha256 `ebf31b5ec517...`.
- test full: under-triage L1-2 14/37 (95% CI 24.1%-53.9%), macro-F1 0.625, ECE after scaling 0.112 (n=191).
- Metrics from ktas-hgb-ebf31b5e-s42 meta.json (M1-04).

The M1-04 HistGradientBoosting baseline (ADR-019), served by the API as the server suggestion.
