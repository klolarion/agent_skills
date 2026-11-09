---
name: work-ledger
description: Records decisions, measurements, corrections, and failures from the current work session into a per-project append-only ledger, each with its source in the transcript or a file, so they can be cited months later in reports, resumes, or posts without redoing the work or guessing. Use when the user asks to record, log, or keep what was decided or measured (기록해, 장부에 남겨, 결정 기록, 실험 결과 기록), runs /work-ledger, or after the commit reminder. Also lists earlier sessions with unrecorded work. Not for activity logs, conversation summaries, or memory for the next session.
license: Apache-2.0
metadata:
  version: "0.0.1"
---

# Work Ledger

Keep evidence of the work as it happens: what was decided and why, what was measured and under which conditions, what was wrong and how it was found, what failed. Months later, a resume line, a report, or a blog post can cite an entry instead of reconstructing it from memory.

Two rules carry the skill:

- **No source, no number.** Every value comes from the bundle the script extracts or from a file you open, and cites it. Anything else is marked `[needs check]`, never filled from your recollection of the conversation, which compaction may have altered.
- **The user decides what is written.** Draft, show, wait for approval, then append. The ledger is append-only; old entries are never edited.

Paths below are relative to this skill's directory. Run `scripts/ledger.py` with `python3` (3.9 or later); it prints UTF-8 JSON. Version 0.0.1 reads Claude Code transcripts only.

## Procedure

1. **Find the ledger.** Run `scripts/ledger.py where` from the working directory. If it reports no ledger, ask the user where to keep it and the default visibility (`public`, `private`, `internal`), and record nothing until they answer. Then run `scripts/ledger.py configure --repo <repository root> --ledger <file> --visibility <word> --lang <ko|en>`. If they choose a path inside the repository being worked on, say once that the ledger would then be published with it.

2. **Extract.** Run `scripts/ledger.py extract`. It reads the current session's transcript after the last recorded point and returns a bundle: session metadata (models, token usage, commits, changed files, time range) and signals (what the user typed, with the reply it answered when the message is short; notable commands and their results; lines with numbers; lines where you reported something wrong or fixed; failed commands; interrupts). Secret-looking values and email addresses are already masked. Messages already recorded or skipped in another session's transcript are left out (a continued session repeats them). Read only the bundle, not the transcript file. The transcript keeps your final replies but not always what you wrote between tool calls, so a correction you only mentioned mid-turn may be missing; do not fill it from memory. For another session, run `scripts/ledger.py pending` to list this repository's sessions with unrecorded messages and pass `--session <id>`. A continued or compacted session can have a new ID; check `pending` when the user's work spans sessions.

3. **Select and draft.** Follow [format](references/format.md): choose what to record, at most 10 entries, and draft each one with a placeholder ID, kind, visibility, a summary line, and its fields and sources. Use the bundle's `ref` values as sources. Take numbers and their conditions only from the bundle or a file you open. Before showing the drafts, run them through `scripts/ledger.py append --check < <file>` and fix what it names.

4. **Show and ask, in one message.** Show the drafts, then the excluded candidates one line each, then ask: record all, drop some, or change some. If the bundle is not `complete`, say how much remains. Do not write before the user answers. If nothing is worth recording, say so and run `scripts/ledger.py skip --session <session> --to <range.to_line>` so the next run starts after it.

5. **Append.** Write the approved entries to a temporary file and run `scripts/ledger.py append --session <session> --to <range.to_line> < <file>`. It checks each entry (title format, length, a source, no secret-looking value), assigns IDs, appends, and moves the cursor. If it refuses, fix what it names and run it again. Report the IDs it assigned. If the bundle was not complete, offer the next extraction.

## Commit reminder

`scripts/ledger.py hook` is a `PostToolUse` hook for Claude Code. After a successful `git commit` in a repository that has a ledger, it appends a line to the commit index in the state directory (time, session, transcript, branch, commit, session title) and shows one line suggesting `/work-ledger`. It never calls a model and never blocks the session. In a repository without a ledger it does nothing. Install it only when the user asks:

```json
{"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 <skill dir>/scripts/ledger.py hook"}]}]}}
```

The commit index answers "which session made this commit?", so that session can be found and resumed even when nothing was recorded.

## Rules

- Never print secret values, in drafts, entries, or your own messages. The script masks known formats and secret-named assignments; if you still see a value, do not repeat it.
- Never write an entry the user did not approve, and never edit or delete an existing entry. A change is a new entry that supersedes the old one.
- Do not record activity, summaries of the conversation, or things the user said only in passing.
- Do not record in a repository with no configured ledger, and do not move the ledger into a public repository without the user saying so.
- Visibility is required on every entry. When unsure, choose the stricter one.
