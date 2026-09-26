# ADR-007: KTAS instead of MIMIC-IV-ED for the urgency model

## Status

Accepted, 26 Sep 2026 ([M0-01]). Owner decision of 26 Sep 2026.

## Context

The original plan trained the urgency model on MIMIC-IV-ED (about 425,000 US emergency visits). That needs PhysioNet credentialing (a CITI course and a reference), which is slow, and the owner decided on zero cost and no PhysioNet. Training on synthetic triage data would only teach the model the rules we wrote into that data.

## Decision

- Train and evaluate the urgency model on the public **KTAS dataset** (1,267 real Korean emergency department records, PLOS ONE). It is fetched from the paper's supporting information without a login (a Kaggle copy is optional); the paper is cited and the data is not redistributed in the repo.
- Everything else uses synthetic data: Synthea v4 patients in HAPI FHIR and generated scenarios.
- No MIMIC-IV-ED and no PhysioNet data anywhere.

## Consequences

- The dataset is small, so every baseline (logistic regression, gradient boosting, zero-shot LLMs, fine-tuned Laya) is published, and a plain tabular model may win. If it does, the README says so.
- Results come from Korean ED data and say nothing about UAE or UK patients until tested there; the README states this.
- The primary metric is under-triage on levels 1-2, reported with its confidence interval given the small test set.
