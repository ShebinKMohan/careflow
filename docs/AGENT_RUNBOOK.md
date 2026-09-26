# CareFlow agent runbook

Binding for every coding agent working the CareFlow backlog (Linear project "CareFlow", team "Shebin - Personal Workspace", issues `[M0-01]` … `[M6-04]`). Read this, then `docs/CONVENTIONS.md` (its "Owner decisions" section overrides `docs/PLAN.md`), then `docs/PLAN.md`.

The goal: the backlog runs first to last with no owner present. Do not wait for the owner except in the cases listed under "Human stops".

## 1. Picking the next issue

1. List the project's issues. Skip any issue labelled `needs-owner` or `deferred` (today: `[M0-00]` owner prerequisites; `[M6-04]` is handled in step 5). `[M5-06]` TraceBisect is active: use TraceBisect only as a read-only pinned dependency and never change its repo.
2. Take the **lowest code** (M0-01 < M0-02 < … < M6-04) whose status is Backlog or Todo and whose "blocked by" issues are all **Done**. Three issues wait for higher codes, and this rule handles them: M1-12 runs after M1-16, and M5-01 runs after M5-02 and M5-03. The graph has no cycles, so there is always a next issue until the backlog is finished.
3. Default to one issue at a time. Two issues may run concurrently only if both are parallel-safe: each touches no Docker services, no local LLM, no Alembic migration and no file under `packages/contracts/`, and each runs in its own git worktree.
4. Run the whole backlog under `caffeinate -dimsu` (Mac on power, lid open or external display).
5. The unattended run is complete when `[M6-03]` is Done. `[M6-04]` then runs only up to a draft PR (see its issue) and stops for the owner.

## 2. Status flow

- Start: move the issue to **In Progress** and create branch `she-<n>-<slug>` from `main`.
- Finish: acceptance criteria met, evidence written, PR merged, then move it to **Done** and post the done report (section 10).
- Stuck: post a comment that starts with `BLOCKED:` giving what failed, the commands and trimmed output, what was tried, and what would unblock it. No label is needed. Leave the issue In Progress, then move on to the next issue whose blockers are all Done. Retry blocked issues after each later issue finishes.
- If the Linear tool is unavailable, write the same text to `docs/evidence/<CODE>.md` and continue.

## 3. Decision policy

- Never wait for the owner. When the issue leaves a choice open, take the default written in the issue, in `docs/CONVENTIONS.md` ("Autonomy defaults") or in this runbook. If none exists, take the simplest option that keeps the safety rules and zero cost.
- Record each non-trivial decision in `docs/decisions/ADR-0NN-<slug>.md` (Status, Context, Decision, Consequences) and in the Linear done report.
- Contracts win: `packages/contracts/openapi.yaml` is the source of truth for routes, roles and bodies. Add anything missing to the contract, regenerate, and note it in the PR.
- A missing prerequisite (data file, gated model, API key, owner clips, blocked website) is never a stop. Use the documented fallback, or mark the suite or test `not_run` with the reason, and continue. Never invent a number.

## 4. Retry policy

- A failing check (test, lint, build, CI job, download): diagnose, fix and retry, up to **3 attempts** per distinct failure.
- After the third failure: post `BLOCKED:` (section 2) and move on.
- Never weaken an assertion, loosen a safety threshold, add retries to flaky tests, or skip a safety test to get green. Flaky tests are recorded in Linear or `docs/FLAKY.md`.

## 5. Commit, push and merge

- Small commits with conventional messages (`feat(intake): …`, `fix(api): …`, `test(e2e): …`, `docs: …`), ending with the attribution lines the harness supplies.
- Each issue ends with its work on `origin/main`: `gh pr create`, `gh pr checks <pr> --watch`, then `gh pr merge --squash --delete-branch` once checks pass and `docs/evidence/<CODE>.md` exists. No human review is needed (private repo).
- If GitHub Actions is unavailable because of the free-minutes quota: run the same targets locally (`make check`, `make e2e`), save the output as evidence, note "CI criterion deferred: Actions quota" and merge. Never touch billing.
- Throwaway failure-demo branches (for example M5-07 fake key, M6-02 copy check) are pushed, their failing run URL is recorded, and then deleted. Delete only branches you created.
- Never force-push `main`. Never change repo visibility, settings, secrets or branch protection. Never create another repository. Never push to, or open issues or PRs on, TraceBisect.
- The Portfolio repo is touched only by `[M6-04]`: work in a git worktree, stage explicit paths only (never `git add -A`), open a **draft** PR, never push to its `main` (that deploys to Vercel production).

## 6. Resource limits (MacBook Pro M3 Pro, 18 GB RAM, ~67 GB free disk)

- Docker total memory <= 6 GB. Stop containers the current issue does not need (`docker compose stop <svc>`; OIE and Langfuse only when their issue needs them).
- One local model loaded at a time. Start `mlx_lm.server` only through `scripts/with_llm.sh` and stop it afterwards. Stop Docker and `mlx_lm.server` before any fine-tune (M3-01, M3-04, M5-02) and restart them after.
- If Docker is not running, `scripts/ensure_docker.sh` runs `open -a Docker` and waits up to 180 s. If it is still down, skip Docker-marked tests with the reason and move to a non-Docker issue.
- Disk: require >= 15 GB free before any download over 1 GB. Delete archives after extraction, keep only the best checkpoint, run `cargo clean` after Tauri builds and `docker builder prune -f` when free space drops below 15 GB.
- CI: ubuntu runners only (never macOS), `concurrency` with `cancel-in-progress`, the e2e job only on pushes to `main`, PRs labelled `e2e` and `workflow_dispatch`, and persona-only seeding (`SEED_SYNTHEA=0`).
- Use `gtimeout` (coreutils) or a Python wrapper, never bare `timeout` (macOS has none).

## 7. Zero-cost rule

- No paid service, paid API, paid GPU, cloud GPU (Kaggle/Colab), paid plan or purchase of any kind. No account sign-ups.
- Hosted API providers run only if the owner has put a key in `.env`. With no key, use the local provider (Qwen3.5-9B MLX) or the replay provider; mark API-only rows `not_run`.
- Subscription CLIs (`codex exec`, `claude -p`) are for offline jobs only, at most 200 calls per issue, never in CI or the live path. Prefer writing synthetic data yourself in-session.

## 8. Secrets rule

- `.env` is gitignored and never committed. `.env.example` lists variable names only.
- `scripts/gen_dev_env.py` fills missing local dev secrets (JWT, signing keys, DB passwords, Langfuse init keys) with random values. It never overwrites or prints a value.
- Only local, generated keys. No GitHub repository secret is needed. CI uses literal throwaway test values. Never echo a secret into logs, Linear or commits.
- If a key is absent, use the local or replay provider; never ask for one and never create one on a third-party site.

## 9. Safety rules (from CONVENTIONS; never trade them for speed or a green run)

1. The rules engine runs on the structured facts before any urgency suggestion and is never overridden by a model.
2. No AI output reaches a patient as medical advice. RAG refuses medical questions and flags the nurse.
3. The nurse confirms every urgency level.
4. Every fact in the doctor briefing carries a provenance tag.
5. No camera frame or raw audio is ever stored; transcripts are session-only.

Also: synthetic data only, the prototype footer on every screen, and no Arabic accuracy percentage anywhere.

## 10. Human stops (the only reasons to stop and wait)

Stop, post a Linear comment starting `OWNER:` that says exactly what is needed, and continue with other unblocked issues:

- spending money or starting a paid plan or trial;
- publishing anything publicly: changing repo visibility, enabling GitHub Pages, creating a GitHub Release, merging or deploying the Portfolio card, posting on any public site or repo;
- changing repository settings, branch protection or secrets;
- deleting data that cannot be recreated (`make nuke`, dropping volumes with results, deleting branches or issues you did not create);
- anything else irreversible or outside this machine and the private careflow repo, including system settings (`sudo`, `mkcert -install`, loading a launchd job) and accepting third-party terms (for example a gated Hugging Face model).

## 11. Done report (Linear comment on every finished issue)

```
DONE: [<CODE>] <title>
Commit/PR: <merge commit SHA> (<PR URL>)
Acceptance criteria:
- [x] <criterion> — evidence: <command> → <key output line> (docs/evidence/<CODE>.md)
- [x] <criterion> — not_run: <reason> (allowed by <rule>)
Decisions: <ADR-0NN title, or "none">
Deviations from the issue text: <what and why, or "none">
Follow-ups: <new issue ids, OWNER: items, or "none">
Resources: peak Docker <n> GB; local model <name or none>; CI <green | deferred: reason>
```
