# Changelog

All notable changes to `skills/deploy-check`. Versions follow SemVer; changing an item ID or the pack schema is a breaking change.

## 0.3.0 - 2026-10-29

Adds a judgment layer on top of the checklist, reads scanner reports that CI already produced as evidence, splits large repositories across parallel workers, and reads the audited repository only through a masking reader. Results from 0.2.0 are not comparable: there are more blockers, and a critical finding now decides the result on its own.

### Changes that affect results

- Blockers 5 → 9. `CORE-AUTH-02` (object-level authorization), `PAY-HOOK-01` (unverified payment webhook), and `AGENT-TOOL-03` (code or shell tools without a sandbox) become blockers; the new item `CORE-SEC-08` is one.
- New items (63 → 65): `CORE-SEC-08` (no default secrets or known-password accounts in production) and the probe item `CORE-DEP-03` (no known high or critical dependency vulnerabilities, from a scanner report).
- A critical finding makes the result "Not ready to deploy" whatever the level. Critical means someone with no special access could exploit it now, wherever it was found: inside an item, outside every item, or by the risk sweep. A condition the repository cannot show (a port reachable from outside, a feature flag) is stated, not used to lower severity; a policy question does not postpone it.
- Item text:
  - `CORE-CI-01` is judged on the pipeline that builds or deploys the production release: it must run the tests or wait for them; tests only on pull requests are Needs confirmation (branch rule), tests only by hand are Fail.
  - `CORE-AUTH-02` names tenant IDs taken from the request and object keys used for signed URLs. `CORE-AUTH-05` fails on the main login path; a verifying main path plus one skipping path is Partial.
  - `CORE-SEC-08` covers accounts created by an admin or invite flow, and code that pads an empty or short value into a working key.
  - `CORE-TEST-01` needs a test that login refuses a wrong credential. `CORE-OPS-01` is Partial when the deploy check calls a missing path or ignores its failure. `CORE-SEC-04` judges HTML served to users.
  - `AGENT-HITL-02` fails when the prompt does not show the action, target, and arguments that will run. `AGENT-TOOL-01` passes only when the executor refuses names outside the tool list. `AGENT-TOOL-02` has a policy question.
  - `AI-LLM-02` counts SDK default retries as bounded. `AI-GRD-01`/`AI-GRD-02` fail texts cover the all-paths case. `KR-PII-04` and `PII-LOG-01`: a request log without the acting account is not an access record.

### Added

- Judgment layer (SKILL.md steps 2 and 8): an independent risk sweep right after the repository map, before the agent sees items or probe results, then critical findings, top risks, fixes grouped by root cause, and a line where the judgment differs from the computed result. It never edits a verdict.
- Scanner reports as evidence (`scripts/gates.py`, `--gate gitleaks=|npm-audit=|pip-audit=|sarif=PATH`): reports CI produced are read, never run; they count only for the commit they were made from, in a clean tree; reports inside the audited repository are refused and secret values are dropped on load.
- Running mode (`scripts/repo_stats.py`): small repositories run in one session; large ones propose parallel workers with the cost stated, and silence never counts as consent. Worker prompts (`references/worker.md`) share one repository map, start item workers after the sweep with its findings, and keep each worker in its area.
- Masked reading (`scripts/mask.py`): the agent reads and searches the audited repository only through it. Code, routes, SQL, prompts, and messages stay readable; a string value shows only as its length and kind (placeholder, key format, entropy); anything assigned or passed to a password, secret, token, or key name is masked whole, including derived passwords.
- Report self-check (`scripts/check_report.py`): after writing a report file, checks rows against the selection, the blockers and gap tables against the verdicts, one fix per gap item, broken table rows, and that every cited `path:line` is a full path to a tracked file within its length.
- Report: snapshot line (commit and working-tree state), critical findings, top risks, fixes by root cause, unwired code, running mode. An existing report file is never overwritten.

### Changed judging rules

- Pass basis on every Pass and Partial row: `re-opened`, `probe`, `gate:<tool>@<commit>`, or `absence`; `worker` only for Partial. Only `re-opened` and `gate` rows may be called verified.
- Mixed paths: the protection on some paths or services but not others is Partial; Fail means none has it; an item without `partial` text is all or nothing.
- Evidence: only git-tracked files; memory, `CLAUDE.md`, and docs are claims to verify. Internal-only services need network evidence in the repository. Every verdict is checked against the item's own wording.
- `profile.md`: a human confirmation step does not turn `agent` off; single-shot routing is not an agent unless the chosen intent writes, sends, or deletes; disabled agent code is reported with the blockers it would trigger.
- Secrets: never printed or described in a way that reveals them, in the report or in the agent's own tool output.
- Region pack freshness uses the oldest citation check date (`oldest_verified`), not the edit date.

### Fixed

- Six false Passes in probes: a step name mentioning a test runner, `continue-on-error`/`allow_failure`, commented-out gitleaks and npm audit steps, an unknown secret format (pattern-only Pass is now medium confidence), and an error tracker initialized only outside production code.
- `CORE-CI-01` probe returns `review` when a release pipeline neither runs nor waits for the tests.
- `packs.py --item` always prints a `partial` key; `packs.py --help` lists the entry keys.
- Pre-release review:
  - `probes.py` could run a `filter.<name>.clean` command from the audited repository's git config while checking whether the tree is clean (the `--gate-commit` path). It now blanks every configured filter and uses `git diff-index`; tracked symlinks that leave the repository are never read.
  - `mask.py` masked more shapes: defaults inside references (`${DB_PASSWORD:-x}`), YAML block values and multi-line strings after a secret name, the rest of the line after a secret-named key in config files, `--password x`, `-u user:x`, XML elements, netrc, comparisons with a secret, URL passwords with no user name, and PGP key blocks.
  - Instructions: one `CORE-CI-01` rule between the pack and `judging.md`; Pass basis wording; blockers and levels; provider-side spend caps and IdP settings count only from tracked config; `CORE-AUTH-04`, `CORE-OPS-02`, `CORE-SEC-05`, and `AI-GRD-02` texts no longer overlap or leave a gap; no exception for running code "if the user asks".
  - Clean errors instead of tracebacks for malformed scanner reports, profiles, selections, and packs; `check_report.py` reads tables without outer pipes and verdicts with a leading symbol; `--gate-commit` needs at least 7 hex characters; large files are counted by `repo_stats.py` and reported as not checked by `check_report.py`.

### Evaluation

| Fixture | Role | Result on 0.3.0 |
| --- | --- | --- |
| `skill-test` (seeded defects, 65 items) | the skill was tuned against it | 65/65, no false Pass, no missed blocker, every seeded critical finding |
| `skill-holdout` (mostly passing, 48 items) | never used to tune the skill | 47/48, no false Pass, no missed blocker, both critical findings, all traps |
| real repositories | development runs | not scored |

### Upgrading

- Re-link or re-copy the whole skill folder: there are new scripts and `references/worker.md`.
- Python 3.9 or later is required: the agent reads the repository through `scripts/mask.py`.
- Pass the scanner reports your CI produces with `--gate` to settle `CORE-DEP-03` and strengthen `CORE-SEC-01`.

## 0.2.0 - 2026-10-27

Fixes from unscored development runs.

- New L1 blocker `CORE-AUTH-05` (login verifies credentials before issuing a token). `CORE-AUTH-04` now covers request-time token verification only. Gold for the sample adds it as Needs confirmation (the sample has no login route).
- SKILL.md step 5: besides re-opening cited lines, trace every entry point the item is about to the cited protection.
- Report: new "Critical findings outside the packs" section under the result line, with strict criteria (`judging.md`); it does not change verdicts or the level. The conversation shows the summary and offers the full table as a file. Evidence paths must be full repo-relative paths.
- `judging.md`: production-reachable code only; with several services every exposing service must meet an item, otherwise Partial, with per-service evidence; policy-dependent items; three new rationalizations.
- Pack schema: optional `policy_question` on review items (validated), passed through by `select_items.py`; added to `AI-GRD-04` and `PII-ENC-02`.
- `profile.md`: only signals that production entry points reach set a trait or side-effect level.
- `CORE-SEC-01` probe: CodeBuild `secrets-manager` / `parameter-store` blocks and secret-manager ARNs are references, not secrets; env files holding only public bundler keys (`VITE_`, `NEXT_PUBLIC_` …) are not secret files but are still scanned. SKILL.md lets a probe `fail` be lowered to Needs confirmation (never Pass) when every location is shown to be a reference or public value.
- `CORE-CI-01` probe: `Jenkinsfile` and `buildspec*.yml` recognized (also by `CORE-SEC-03` and `CORE-DEP-02`); tests found only there return `review` because their trigger is outside the repo, unless the Jenkinsfile declares a push trigger. Gradle and Maven test tasks recognized.
- `CORE-DEP-01` probe: a standalone `requirements.txt` is checked even when other manifests exist (it was skipped, a false Pass); Gradle and Maven pass with exact versions or `gradle.lockfile` and fail on dynamic versions or ranges. Pack text updated.
- `CORE-SEC-05`: production list of the service's own HTTPS origins, in code or config; development origins in it are Partial.
- 18 new regression tests (54 total).

## 0.1.3 - 2026-10-26 (not released separately; shipped in 0.2.0)

Fixes from a probe review.

- `probes.py` never runs the audited repo's `core.fsmonitor` program (`git -c core.fsmonitor=false`).
- `CORE-OBS-01`: only an init/configure call passes; a declared dependency alone is Fail with its location.
- `CORE-CI-01`: installing a test runner no longer counts as running tests; comments, file names (`tox.ini`, `jest.config.js`) and swallowed failures (`|| true`) ignored; `./mvnw`, `tox`, `make test` recognized. Pack criteria now say the test failure must fail the build (core pack `updated: 2026-10-26`).
- `CORE-SEC-02`: anchored patterns (`/.env`, `/.env*`) count.
- `CORE-DEP-01`: workspace members (npm/pnpm/yarn/bun, Cargo, uv) are covered by the root lock file when the root declares a workspace.
- `CORE-SEC-01`: Stripe, Toss, Google API key and current OpenAI key formats detected; field names such as `password_hash` ignored; hits only in test or fixture paths return the new outcome `review` instead of an automatic blocker Fail (SKILL.md step 3, `judging.md`).
- `packs.py`: `--item` without IDs or a missing path prints usage and exits 2 instead of a traceback.
- `select_items.py`: `--stage` is required instead of silently defaulting to `production`; a missing or unknown stage in `--profile` is a usage error, not a traceback; `profile.md` L3 wording made unambiguous.
- `report.md`: probe items with outcome `review` are marked "manual, not deterministic".
- 10 new regression tests (36 total).

## 0.1.2 - 2026-10-21

Fixes from blind eval run 2.

- `report.md`: writing the report file is the only allowed write and only on request; section headings in the user's language.
- `packs.py --help`.
- Clarified criteria: `CORE-AUTH-01` (fail = no auth mechanism), `CORE-LOG-01` (no logging setup), `CORE-BAK-01` (database managed outside the repo → Needs confirmation), `AGENT-TOOL-02` (server-side validation only).
- License metadata: Apache-2.0.

## 0.1.1 - 2026-10-21

Fixes from blind eval run 1.

- New item `CORE-AUTH-04` (unforgeable session credentials).
- `packs.py --item <ID>` to read single entries; SKILL.md step 4 uses it.
- Scripts print UTF-8 on Windows consoles.
- `judging.md`: only served pages count; library defaults count only when documented, enabled, and cited.
- `AI-GRD-02` scope excludes tool arguments (judged by `AGENT-TOOL-02`).

## 0.1.0 - 2026-10-21

First version.

- 6 packs (`core`, `ai`, `agent`, `pii`, `payment`, `kr`), probe / review / interview layers, levels L1–L3 with 4 blockers.
- Scripts: `packs.py` (restricted YAML parser), `select_items.py`, `probes.py`.
