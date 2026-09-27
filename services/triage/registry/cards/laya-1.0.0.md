# laya 1.0.0

Status: released. Registered 2026-09-27T07:55:01+00:00. Research prototype; not for clinical use. The model only suggests a level: the rules run first and a nurse confirms every level.

- Artifact: `services/triage/artifacts/laya/laya-ktas-ebf31b5e-s42/onnx-int8` (git-ignored), manifest sha256 `201e43887d195cab5999aa771d6f29e6a2d07f0e1adebcd050173b17ade38805`, 7 files, 606 MB.
- Data: KTAS (PLOS ONE 2019, CC BY 4.0), split `ktas_v1.json`, CSV sha256 `ebf31b5ec517...`.
- test full: under-triage L1-2 12/37 (95% CI 19.6%-48.5%), macro-F1 0.662, ECE after scaling 0.097 (n=191).
- test kiosk partial: under-triage L1-2 14/37 (95% CI 24.1%-53.9%), macro-F1 0.670, ECE after scaling 0.127 (n=191).
- Metrics from laya_ktas_20260927.json (the fp32 run, M3-01).

Laya fine-tuned on KTAS (M3-01, ADR-042), shipped to the kiosk as the split ONNX bundle (ADR-043): an int8 weight-only encoder (MatMulNBits, 8 bits, blocks of 32) with fp16 embeddings, and an fp32 head, with the question it was trained on (question.json). On the 50 synthetic golden states it agrees with the fp32 model on every argmax (max |dp| 0.024). The test-split parity check is pending (test phase).
