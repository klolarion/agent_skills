# Report

Write the report in the user's language, including the section headings below. Keep item IDs, pack names, levels, and verdict words (Pass, Partial, Fail, N/A, Needs confirmation) as they are. Writing the report file is the only write this skill may make, and only when the user asks for a file: use the path the user gives, otherwise `deploy-check-report.md` at the audited repository root. If that file already exists, never overwrite it: write to the same name with the next free number (`<name>-2.md`, `<name>-3.md`) and say so.

## Conversation and file

- **Conversation (default):** show everything from the result line through "Questions for you", then "Next step". In "Gap to target level" show at most 10 rows, lowest level first (L1 before L2), leaving out blockers already shown in "Blockers", and in "Fixes by root cause" at most the 5 fixes that close the most items or the most severe ones; say how many more there are. In "Critical findings" show at most 8 rows, unconditional ones first, merging findings of one item that one fix closes into one row; say how many more are in the file. Write a long report file in several writes (summary sections first, then the item table), not in one tool call. End with one line offering the full report, including "All items", as a file.
- **File (when asked):** the whole template.

Long tables in a terminal are hard to read. The summary must stand on its own: a reader who sees only the conversation part should know whether to deploy, what is most dangerous, what blocks it, and what to answer.

## Template

```markdown
# Deploy check: <repository name>

**Result: <Not ready to deploy | L1 met | L2 pending (3 to confirm) | ...> · target <Lk> · blockers <n> failed · gap to target <n> items (<m> distinct fixes) · critical findings <n>**
<one line, only when the judgment layer disagrees with what the result line suggests>

Profile: traits <...> · forms <...> · stage <...> · region <...> · agent side effects <...>  (inferred from: <signals>)
Packs: core <date> · ai <date> · kr <date>, citations checked <oldest_verified>  [warning if that check is more than 180 days old]
Risk sweep: done | skipped (<reason>)
Snapshot: commit <short sha> · working tree <clean | tracked changes | untracked files imported by tracked code>

## Critical findings
(exploitable now; criteria in judging.md. Write "None" if there are none. Findings with no condition first)
| Source (item ID / outside / sweep) | Finding | Evidence | What an attacker can do | Condition, if any |

## Top risks
(at most five, critical findings first; model judgment, not a computed result)
1. <risk> — <why it ranks here> (<item IDs or sweep>)

## Blockers
| ID | Item | Verdict | Evidence | Fix |
(only failed or partial blockers; write "None" if there are none)

## Gap to target level
| ID | Item | Level | Verdict | Fix |
(Fail and Partial items at or below the target, sorted by level then ID. No evidence column: evidence is in "All items" and "Blockers", and writing it twice only lengthens the report. Fix is the row number in "Fixes by root cause")

## Fixes by root cause
| # | Fix | Items it closes |
(one row per distinct change; every Fail and Partial item in the gap appears in exactly one row)

## Questions for you
Grouped by area. Each question names its item ID. Include interview items and the policy questions whose answer would change a verdict.

## All items
| ID | Item | Level | Method | Verdict | Basis | Evidence | Confidence |
(every selected item plus every excluded item as N/A with its reason; row count = selected + excluded)

## Evidence checked / not available
- Checked: files, configs, probe output used; running mode (single session or workers) with the `repo_stats.py` line counts; with workers, the number of workers and the token numbers the environment reports, saying what they measure (often each worker's final context size, not its total use) and that the coordinator's own use is not included
- Not available: what would raise confidence (console settings, deploy platform, documents outside the repo)
- Unwired code: side-effecting code production does not reach yet, and the blockers that would apply once enabled

## Next step
One concrete action with the highest effect: the first critical finding, otherwise the top risk, otherwise the first failed blocker.

## Suggestions for the check packs
Only if you noticed a missing or unclear check, including non-critical sweep findings no item covers. Do not judge items that are not in the packs.
```

## Rules

- The first bold line must let the user decide in one glance: deploy or not, and why. If a critical finding exists, the result is never presented as ready, whatever the level.
- Row count of "All items" must equal selected + excluded from the selection output. Count before finishing, also when the table goes only to the file.
- Evidence paths are full paths from the repository root, never shortened with `...`.
- After writing a report file, check it with `scripts/check_report.py` (see SKILL.md step 9), fix every error it lists, and run it again.
- Every Pass and Partial shows its basis (`re-opened`, `worker`, `probe`, `gate:<tool>@<commit>`, `absence`; see judging); for Fail, Needs confirmation, and N/A rows it is optional. Never call a row verified unless its basis is `re-opened` or `gate`.
- Region pack rows: add "existence only, legal review required" once in the section header, not on every row.
- Mark probe results that were judged by hand as "manual, not deterministic". This includes every probe item whose outcome was `review` and every probe `fail` you lowered.
- "Top risks", "Fixes by root cause", and the disagreement line are the judgment layer. Label them as such; never edit a verdict to match them.
