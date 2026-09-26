# careflow-rules

CareFlow's safety rules engine: pure Python, no I/O, no AI. `evaluate(facts, now)` takes `StructuredFacts` from `careflow_contracts` and returns the alerts raised, the facts still needed and the NEWS2 score. It runs before any urgency suggestion, and no model can override it (safety rule 1).

```python
from datetime import UTC, datetime

from careflow_rules import evaluate

# The caller passes the time; rules never read the clock.
result = evaluate(facts, datetime.now(UTC))
result.alerts  # careflow_contracts.Alert list, critical first
result.needed_facts  # e.g. vitals.respiratory_rate for the nurse
result.news2  # News2Result, or None under 16
```

- **NEWS2** (`news2.py`): Royal College of Physicians, NEWS2 (2017), SpO2 Scale 1. A partial score (kiosk vitals only) underestimates risk: always label it partial and never show it as a full score. `merge_vitals()` combines device and nurse readings so the nurse value wins.
- **Rules** (`rules/`): `SEPSIS_NEWS2` (M1-01). FAST, chest pain and the NICE NG143 child rules come in M1-02.
- **Versioning:** `RULESET_VERSION` is `2026.1.0`. Any threshold change bumps it and adds a `CHANGELOG.md` entry.
- **Tests:** boundary tables for every NEWS2 parameter, Hypothesis monotonicity properties (500 examples each), sepsis cases, and a purity scan that rejects network, database, provider, file, clock and randomness use. `careflow_rules.testing` builds synthetic facts for tests and scenario suites.

Research prototype. Synthetic data only. Not for clinical use.
