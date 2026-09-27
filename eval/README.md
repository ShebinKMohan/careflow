# CareFlow evaluation

The `careflow-eval` workspace package evaluates the actual rules engine and English fact
extractor. Its patients are explicitly synthetic. Dataset review is automated and is **not
clinician review or clinical validation**. Expected urgency levels are scenario metadata,
not evidence that a model is safe for triage.

Install the workspace with `uv sync --locked --all-packages`, then:

```sh
uv run careflow-eval list
uv run careflow-eval validate intake_en
uv run careflow-eval run rules
uv run careflow-eval run extraction_en --replay
```

`rules` runs the existing YAML scenarios in `eval/scenarios/rules/` without loading a model.
`extraction_en` builds approved question/answer turns, invokes the production `LlmExtractor`,
and evaluates rules using extracted facts plus the supplied demographic and vital context.
Expected facts enter scoring only; they are never passed to the extractor.

## Data and admission

`eval/datasets/intake_en/` contains the English corpus: eight chest-pain, six stroke, eight sepsis,
six child, four breathing, and eighteen routine cases. Each YAML file must match
`eval/schemas/scenario.schema.json`, including `synthetic: true`, patient demographics, patient
turns, device/nurse vital inputs, generation provenance and expected facts/alerts. Explicit
patient statements are gold even when the patient volunteers them in an answer to a different
question. Device and demographic inputs are context, not transcript extraction targets.

The default loader requires a distinct known model family for automated review, a review
timestamp and an exact content hash. Editing a reviewed case invalidates approval. Named
device scenarios are resolved into the hashed review content, so changing their values also
invalidates approval. Duplicate keys, unsupported gold, invalid facts, PHI denylist matches,
and disagreement with the actual rule engine fail validation. The denylist is a limited
screening check; synthetic authorship and review remain required.

Drafts live under `_inbox/`. `--include-unreviewed` is an explicit diagnostic option and is
recorded in reports. It does not approve a dataset. Review decisions and raw model outputs
are retained in `eval/datasets/review-log.jsonl` and `eval/datasets/review-receipts/`; the
repository-level
`datasets/REVIEW.md` records the pre-baseline gold audit.

## Live recording and replay

Start the one local model only through the repository wrapper:

```sh
mkdir -p .run
scripts/with_llm.sh uv run python scripts/eval_local.py baseline \
  --launch-log .run/baseline-launch.log \
  --record-root eval/replays --output-root eval/reports 2> .run/baseline-launch.log
```

Use a fresh launch-log file for each attempt. Existing recordings are never overwritten;
choose a new record/output root for later baselines. The helper observes the actual endpoint,
selects the exact pinned snapshot, retains its resolved configuration and launch evidence,
and warms the production extraction prompt/schema with an empty transcript before timing
cases. It does not use any scenario or gold in warm-up. Evidence is retained under
`<output-root>/local-runs/<timestamp>-baseline/`. The helper starts no server itself.

For custom providers, use the lower-level `careflow-eval run extraction_en --config ...`
with an observed `--model-receipt ...` and `--record ...` directory.

A model receipt identifies the actual Hugging Face model and pinned revision, launch source,
configured `base_url`, requested `served_model`, and observed `server_model_ids`. A logical
alias such as `qwen3.5-9b-4bit` alone does not prove which weights were served. Preserve the
launch evidence and inspect `/v1/models` for the current process. Warm the model before a
timed baseline and record that warm-up separately. The wrapper stops only a server it starts.

`--record DIR` and `--replay DIR` both use `DIR/<suite>/`. Bare `--replay` uses
`eval/replays/<suite>/`. Recording refuses to overwrite existing evidence. Cassettes use the
canonical provider's request keys and retain raw responses, including invalid model JSON.
Receipts bind inputs, prompt, ruleset, normalization, model, outputs and historical duration.
Missing or changed cassettes fail; replay never silently calls a live model.

Replay reproduces recorded extraction/rules outputs and historical latency metrics. Its
own wall time is separate and is not a model-speed measurement. A provider exception or
timeout without a replayable response remains an explicit incomplete recording; it cannot
be presented as an exact replay or silently dropped from scoring.

## Metrics and reports

Reports go to `eval/reports/<suite>/<UTC timestamp>.json` and `.md`, with a separate `LATEST.md`
for each suite. `--output DIR` changes the report root, not the suite subdirectory. Reports
include source and dataset provenance, prompt/ruleset/model identity, per-case results,
per-key extraction scores, per-rule alert counts, and failing scenario IDs.

Field precision/recall/F1 use micro counts. A wrong value contributes one false positive and
one false negative; duplicates cannot reuse one matching gold value. Matching treats booleans
distinctly, tolerates numeric differences up to 0.1, normalizes text case/spacing, and uses a
small versioned chief-complaint synonym map. Alert metrics include recall and the false-alert
rate on benign cases. Other metrics include extraction incompleteness, dropped facts and
p50/p95 extractor latency (including retry and recording overhead). Undefined metrics remain
`null`/`n/a`, not zero.

The CLI exits nonzero for invalid data, failed review, operational failure or incomplete
execution. A completed evaluation can report poor quality and still exit zero: completion
describes execution, not a clinical pass. CI asserts the fixed recorded baseline separately.

## Authoring and independent review

In-session coding-agent authorship is supported. Alternatively, generation uses the configured
subscription CLI only in the offline runtime:

```sh
CAREFLOW_RUNTIME=offline uv run careflow-eval generate \
  --suite intake_en --n 50 --writer-model <actual-supported-model-id>
CAREFLOW_RUNTIME=offline uv run careflow-eval review --judge cli \
  --config config/providers.eval-claude.yaml --reviewer-model claude-sonnet-5
```

Generation writes drafts and clears approval fields. An unspecified CLI default has no
established model identity and cannot establish reviewer independence. The local review
option uses a different model family and checks wording, fact support/completeness,
rule consistency and synthetic privacy. Model identity is checked against the live endpoint;
approval is bound to the exact content sent. Rejected or concurrently edited drafts remain
unapproved. Keep concrete rejection reasons and review revised content again.

The offline Claude review option requires an existing subscription login and an explicit
model ID. Tools, skills and MCP are disabled; inputs go through stdin in a temporary working
directory. API credentials and alternate-provider overrides are removed from the child
environment. Retained CLI evidence verifies the selected model against principal token
accounting and reports auxiliary model usage separately. The repository's 200-CLI-call
per-issue limit includes retries; the operator must track totals across separate invocations.

For local review, use the wrapper/helper with `review --batch-size 1 --timeout 300` and a
fresh `--launch-log`. The first corpus exposed repeated incorrect rule interpretations in
Qwen's automated reviews; rejected cases were preserved and escalated to the independent
offline reviewer. A successful structured response alone is never sufficient for approval.

This package does not implement Arabic/RAG evaluation, train urgency models, or use an LLM
as the extraction scorer. Those are separate milestones. Existing `eval/reports/queue/` artifacts
belong to the queue simulation. File paths in this document are relative to the repository root.
