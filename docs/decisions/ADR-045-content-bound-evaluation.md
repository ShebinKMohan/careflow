# ADR-045: Content-bound synthetic extraction evaluation

Status: Accepted

## Context

M1-18 requires an English extraction baseline and an offline repeatable evaluation harness.
Existing rules scenarios already exercise the production rules engine, but generated labels,
review claims, provider aliases and replay speed can each create misleading results unless
their relationship to the actual evaluated inputs is explicit.

## Decision

Add `careflow-eval` as a uv workspace package with separate `rules` and `extraction_en`
suites. Reuse production question templates, contracts, extractor, rules and canonical
`ReplayProvider`; keep expected extraction labels outside the model's input. Score fields
with deterministic one-to-one matching and versioned normalization. Report undefined metrics
as undefined and quality failures as measured outcomes.

Require explicitly synthetic scenarios, full authorship provenance and a content-bound
automated review by a known different model family. Resolve named device inputs into the
reviewed hash. The coding agent may author drafts in-session; review supports the configured
local model or an offline subscription CLI with an explicitly identified model. Local Qwen
review exposed repeated rule-interpretation errors, so unresolved cases require a stronger
independent reviewer. A review is never described as
clinician approval. Review receipts retain exact hashes, raw model responses and decisions;
changing a draft during a review cannot delete the newer content or approve it implicitly.

Bind model receipts to the configured endpoint/request model and observed server model IDs.
Retain pinned launch evidence separately because an endpoint's alias alone does not verify
weight identity. Record source provenance, dataset/prompt/ruleset/normalization bindings and
per-case raw replies. Strict replay verifies recorded outputs and retains historical model
latency; report replay execution time independently. The first live baseline exposed one
confirmed extraction timeout during structured correction. Replay may reproduce that terminal
failure event from its immutable ordered request receipt, only after validating its partial
cassette, final timeout outcome, bindings and endpoint evidence. Canonical replay still serves
all actual replies; the confirmed terminal event raises `TimeoutError` into the real extractor.
The actual extraction/rules output hash must match. Report this event explicitly and keep the
original measured latency separate from replay wall time. Never fabricate the missing model
reply or rewrite the historical receipt's reply-only `replayable` flag. Ambiguous cancellations
and unsupported provider failures remain incomplete.

Offline CLI review disables tools, skills and MCP and runs in a temporary directory. It
requires subscription authentication, excludes API credentials and alternate-provider
overrides, and verifies the requested model against the CLI's principal token accounting.
Retain auxiliary model usage rather than claiming only one model was involved. Enforce a
per-invocation call cap and track the runbook's per-issue total across invocations.

## Consequences

The same recorded baseline can be checked in CPU-only CI without a model or hosted API.
Changes to inputs invalidate review/replay admission rather than quietly reusing old evidence.
The first corpus is deliberately small, English-only and synthetic. Its field/alert scores
describe agreement with those reviewed labels and do not establish diagnostic accuracy,
clinical safety, population coverage or acceptable real-world undertriage.

Automated review costs local inference time and can reject cases that need correction.
Rejections and model errors are preserved instead of weakening the gate. Recordings contain
synthetic transcripts only; this workflow is not authorized for real patient data.
