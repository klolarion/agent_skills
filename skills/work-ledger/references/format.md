# Ledger format and what to record

## Contents

- [The ledger](#the-ledger)
- [Entry](#entry)
- [Fields by kind](#fields-by-kind)
- [Sources](#sources)
- [What to record](#what-to-record)
- [Visibility](#visibility)

## The ledger

One Markdown file per project, append-only. `scripts/ledger.py append` creates it with a header that tells any later reader (a session writing a report, a resume, or a post) how to read it. Never edit or delete an entry. When something changes, append a new entry that says "supersedes <ID>".

The ledger keeps decisions, measurements, corrections, and failures. It does not keep activity ("what I did today"): commits, the commit index, and the transcript already hold that.

## Entry

```markdown
## {D} · decision · private
Tenant isolation uses database row-level security, not filters in application code.
- Why: one missed filter leaks another tenant's rows; a policy applies to every query.
- Rejected: a shared query helper; nothing stops a new query from bypassing it.
- Decided by: agent proposed, user approved
- Source: a1b2c3d4#e5f6a7b8
```

- Title line: `## {X} · <kind> · <visibility>`. Write the placeholder `{D}`, `{M}`, `{C}`, or `{F}`; `append` replaces it with `<date>-<letter><n>` (for example `2026-11-05-D2`). Write the kind and visibility words in the ledger's language (`lang` in `where`); for `ko`, use 결정, 측정, 정정, 실패 and 공개 가능, 개인, 사내.
- Then one summary line that makes sense alone, then the fields: 3 to 5 lines in all after the title. A longer explanation belongs in a separate document; link it.
- Write in the ledger's language. Field labels too (ko: 이유, 버린 대안, 되돌리기, 정한 사람, 값, 조건, 기준 대비, 가설, 무엇이 틀렸나, 알게 된 경위, 고친 것, 시도, 실패 이유, 남은 가설, 출처).

## Fields by kind

| Kind | Letter | Fields |
|---|---|---|
| decision | D | why · rejected alternative and why · reversibility (only when hard to undo) · decided by · source |
| measurement | M | value · conditions (model, runs, scorer, data or version, commit) · compared with (baseline, hypothesis) · source |
| correction | C | what was wrong · how it was found · what changed · source |
| failure | F | what was tried · why it failed · what remains open · source |

- **Decided by:** `user`, `agent proposed, user approved`, or `agent` (inside a scope the user delegated). Resumes depend on this distinction; do not round it up to `user`.
- **On hold:** a deferred decision is a decision with the kind word followed by "(on hold)" (ko: `결정(보류)`). When it is settled later, the new entry says "supersedes <ID>".
- **Reversed within the range:** record the final decision. Add "previous: X, because Y" only when the reason for the change matters.
- **Hypothesis:** a field of the measurement it was tested by, not an entry of its own.

## Sources

Every entry cites at least one source. `append` refuses an entry without one.

- From the bundle: the signal's `ref` (`<session 8>#<message 8>`).
- From a file you opened: `path:line`. From git: the commit SHA.
- From the user's memory, added later: `[memory <date>]` (ko: `[기억 2026-11-05]`).
- No source: `[needs check]` (ko: `[확인 필요]`) next to the value. Never fill a number from your own recollection of the conversation; if it is not in the bundle or a file, it is `[needs check]`.

Numbers keep their conditions. "41/50" without the model, the number of runs, and the scorer is not a measurement.

## What to record

Record a candidate when at least one is true:

1. It is hard to reverse, or it changed cost or results.
2. It is a number whose conditions are in the bundle.
3. Without it, nobody could later answer "why is it like this?"

Skip:

- wording changes and internal renames that are easy to undo;
- bug fixes that did not change a result (a fix that lost or changed results is a failure or a correction);
- intermediate research findings that no decision used;
- activity.

At most 10 entries per run. Rank by: hard to reverse, then numbers, then corrections and failures, then the rest. List every other candidate you considered as one line under "excluded", so the user can bring it back.

Signals that usually mean something:

| Signal | Often |
|---|---|
| `user` with `context`: a short reply ("go with the recommendation", "do it") after options | decision; the content is in `context` |
| `user` pushing back ("that is not what I asked", "undo that") | correction |
| `command` with git push, tag, reset, history rewrite; `rm -rf` | decision (hard to reverse) |
| `command` running an eval, a benchmark, a score, a model batch (`claude -p`, `codex exec`) | measurement, or a correction if it ran without consent |
| `numbers` | measurement, when the conditions are nearby |
| `self`: the agent reporting something wrong, a cause, or a fix | correction or failure, when it changed a result; a routine "fixed the wording" is not |
| `error` repeated, `interrupt` | failure, or the cause of a correction |

## Visibility

Every entry carries one: `public`, `private`, or `internal` (ko: 공개 가능, 개인, 사내). Start from the configured default.

- `internal`: anything about a company's systems, customers, code, or incidents.
- `public`: only when the user says it can be published.
- When unsure, use the stricter one. Later, nobody can tell what was confidential.
