# Flaky and machine-dependent tests

This file lists tests whose outcome depends on the machine or on timing, and how each one is handled. The runbook forbids weakening an assertion, adding retries or skipping a safety test to get a green run. So each entry states what still runs in CI.

## `services/triage/tests/test_train_inference.py::test_predict_returns_three_reasons_fast[hgb]` (M1-04)

- **What happened.** The first CI run of PR #12 (GitHub run 36272705313) failed on one HGB `predict()` call of 21.4 ms. The budget is 20 ms. On the reference machine (M3 Pro), the slowest call was 5.2 ms at the time, and it is 4.1 ms after the fix below.
- **Cause.** A shared GitHub runner is slower and noisier than the reference machine. Per call, the model walks 1,000 trees and computes TreeSHAP.
- **Handling.**
  1. `predict()` was made faster first: the one-thread OpenMP limit now uses a controller built once per model, which saves about 1 ms per call.
  2. The owner decisions (CONVENTIONS) say speed targets are measured and published, never CI gates. So the 20 ms assertion runs on the reference machine (any run without `CI=true`).
  3. On a CI runner, the test issues a warning with the p50 and slowest call when a call exceeds the budget, instead of failing.
- **Still asserted in CI.** Every call must return a contract-valid `UrgencySuggestion` with 3 reasons and a `model_version`.
