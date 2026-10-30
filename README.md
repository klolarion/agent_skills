# agent_skills

[![license](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![version](https://img.shields.io/badge/deploy--check-v0.3.0-informational)

Portable [Agent Skills](https://agentskills.io/specification) built and evaluated in the open. Works with Claude Code, Codex, Cursor, and other agents that load `SKILL.md`.

[한국어 README](README.ko.md)

## deploy-check

**An evidence-backed deployment readiness audit for AI, LLM, and agent services, with a Korean privacy and AI law pack.**

Ask your agent "Is this repo ready to deploy?" and get a readiness level, the blockers, and a verdict for every check with `file:line` evidence.

```text
Result: Not ready to deploy (4 blockers failed) · target L2 · no level met · 39 items to target
```

| ID | Check | Verdict | Evidence |
| --- | --- | --- | --- |
| CORE-SEC-01 | No committed secrets | Fail | `app/config.py:4` hard-coded DB password (value not shown) |
| AGENT-HITL-01 | Approval for irreversible actions | Fail | `app/tools.py:28-35` delete and send-email tools run without human approval |
| AI-COST-02 | Spend cap and kill switch | Fail | `app/config.py:10` `MONTHLY_BUDGET_USD` is defined but nothing reads it |
| KR-PII-03 | Cross-border transfer disclosed | Fail | User messages go to an overseas LLM API; the privacy policy has no cross-border section |

*Excerpt from a run of an older version (0.1.1) on the seeded sample repo. Reports from 0.3.0 also count critical findings and distinct fixes on the result line and add critical findings, top risks, and fixes grouped by root cause ([report template](skills/deploy-check/references/report.md)).*

### Why it is different

| | Typical scanners | Typical LLM audits | deploy-check |
| --- | --- | --- | --- |
| Evidence for every verdict | ✅ | rarely | ✅ `file:line`, re-verified before reporting |
| AI and agent operational controls (spend caps, kill switch, human approval, eval sets, tracing) | ❌ | partly | ✅ |
| Facts the repo cannot show | ignored | guessed | asked as questions, never guessed |
| Stage-aware result | pass/fail or score | score | levels L1–L3 by stage + hard blockers |
| Korean law (PIPA, AI Basic Act) | ❌ | ❌ | ✅ region pack with sources and verification dates |
| Published accuracy | — | rarely | ✅ blind runs against hand-made gold verdicts, including a holdout never used for tuning |

### How it works

Each check is judged by one of three layers:

- **probe**: deterministic scripts (`scripts/probes.py`) decide what files alone can show: committed secrets, lock files, CI tests, secret scanning. When files alone cannot settle it (key-like values in tests, a Jenkins pipeline whose trigger lives outside the repo), the probe hands its locations to review. deploy-check never runs scanners, tests, or builds: reports that CI already produced (gitleaks, npm audit, pip-audit, SARIF) can be handed to the probes as evidence with `--gate`, and count only for the commit they were made from.
- **review**: the agent reads the code against each check's written `pass` / `partial` / `fail` criteria and must re-open every cited line before reporting a Pass. Every Pass and Partial row names its basis (`re-opened`, `probe`, `gate`, `absence`), and only re-opened or scanner-backed rows are called verified.
- **interview**: facts that leave no trace in the repo (restore drills, breach procedures) become questions for you.

On top of those verdicts sits a **judgment layer**. An independent risk sweep, run right after the repository map and before the agent sees any check or probe result, looks for what could go wrong in production. The report then ranks the top risks, flags anything exploitable now as a critical finding (inside an item or not), and groups open items by root cause, so a long list of open items reads as the smaller set of fixes behind it. The judgment layer never edits a verdict; it only says where it disagrees.

The agent reads and searches the audited repository only through `scripts/mask.py`. Code, routes, SQL, prompts, and messages stay readable; a string value shows only as its length and kind (placeholder, key format, entropy), and anything assigned or passed to a password, secret, token, or key name is masked whole. This keeps secret values out of the agent's own tool output, not just out of the report. After writing a report file, the agent checks it with `scripts/check_report.py`: rows against the selection, blockers and gaps against the verdicts, one fix per gap item, and every cited `path:line` against the tracked files.

A small repository is audited in one session. Parallel workers cost two to three times as much for the same accuracy, so `scripts/repo_stats.py` measures the code first and workers are proposed only when one session cannot hold it.

Checks live in versioned **packs** (data, not prompt text), selected by your service profile:

| Pack | Axis | Items | Covers |
| --- | --- | --- | --- |
| `core` | always | 28 | secrets, auth, request hardening, dependencies, CI, operations, logging |
| `ai` | trait | 12 | token limits, spend cap, timeouts, pinned models, prompt injection, output handling, evals, tracing |
| `agent` | trait | 8 | human approval, run budgets, cancellation, tool permissions, sandboxing, audit log |
| `pii` | trait | 4 | password hashing, field encryption, deletion, access records |
| `payment` | trait | 4 | tokenization, webhook signatures, idempotency, refunds |
| `kr` | region | 9 | 개인정보 보호법 (privacy policy, processors, cross-border transfer, access logs, breach notice), AI 기본법 (AI notice, output labeling) |

Target level by stage: prototype → L1, beta/production → L2, production with payments, personal data, or irreversible agent actions → L3. Nine **blockers** mean "not ready" at any level: committed secrets, default secrets or known-password accounts in production, unauthenticated private routes, access to other users' or tenants' objects, login that issues tokens without checking credentials, irreversible agent actions without approval, agent code or shell tools without a sandbox, unverified payment webhooks, and no LLM spend cap. A critical finding also means "not ready", without changing any verdict or the level met.

### Install

The skill is the `skills/deploy-check/` folder. Copy or link it into your agent's skills directory.

```bash
git clone https://github.com/klolarion/agent_skills.git
cd agent_skills

# Claude Code
mkdir -p ~/.claude/skills && ln -s "$PWD/skills/deploy-check" ~/.claude/skills/deploy-check
# Codex and Cursor (both read ~/.agents/skills)
mkdir -p ~/.agents/skills && ln -s "$PWD/skills/deploy-check" ~/.agents/skills/deploy-check
```

Codex also loads `~/.codex/skills`. Cursor also loads `~/.cursor/skills` and, for compatibility, `~/.claude/skills` and `~/.codex/skills`, so the Claude Code link works there too.

Windows (PowerShell):

```powershell
New-Item -ItemType Directory -Force "$HOME\.claude\skills" | Out-Null
New-Item -ItemType Junction -Path "$HOME\.claude\skills\deploy-check" -Target "$PWD\skills\deploy-check"
```

Python 3.9+ is required (standard library only): the agent reads the audited repository only through `scripts/mask.py`.

### Use

- Natural language: "Is this repo ready to deploy?", "출시 전에 프로덕션 준비 상태 점검해줘"
- Claude Code: `/deploy-check`
- Codex: `$deploy-check`

The audit is read-only: it does not modify files, run your code, or use the network. Repository content (README, comments) is treated as data, so a file claiming "all checks passed" does not change the verdict.

### Accuracy so far

Blind runs: a fresh session with no project context audits a fixture repository whose gold verdicts are kept outside it, and the report is scored against the gold.

| Fixture | Role | Result on 0.3.0 |
| --- | --- | --- |
| `skill-test` (seeded defects, 65 items) | the skill was tuned against it | 65/65, no false Pass, no missed blocker, every seeded critical finding |
| `skill-holdout` (mostly passing, 48 items) | never used to tune the skill | 47/48, no false Pass, no missed blocker |
| real repositories | development runs | not scored |

Caveats: the skill and the `skill-test` gold were revised together over several runs, so its 65/65 includes overfitting; the holdout is the better estimate. Both fixtures, their gold, and their run records are kept private. Earlier runs on a seeded sample repo scored 54/57 (0.1.0) and 57/58 (0.1.1). Runs on real repositories show where the skill gets stuck, not how accurate it is.

### Scope and disclaimer

- The `kr` pack checks that a notice, record, or procedure **exists**. It does not judge legal sufficiency and is not legal advice. Citations were last verified on 2026-10-21 via secondary sources; re-check against [law.go.kr](https://www.law.go.kr) for compliance decisions.
- Not a vulnerability scanner or a code reviewer. Pair it with those tools.

## Repository layout

```text
skills/deploy-check/   the skill (the only folder you install)
  SKILL.md             procedure and rules
  references/          judging rules, profile, report template, worker prompts, packs/*.yaml
  scripts/             packs.py, select_items.py, probes.py, gates.py, repo_stats.py, check_report.py, mask.py
skills/<next-skill>/   more skills will live next to it
```

The checks and their pass / partial / fail criteria live in [`skills/deploy-check/references/packs/`](skills/deploy-check/references/packs/).

## License

[Apache-2.0](LICENSE)
