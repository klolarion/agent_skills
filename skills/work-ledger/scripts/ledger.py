"""Pick what is worth recording out of a Claude Code session transcript, and keep the ledger append-only.

Usage:
    python ledger.py where                                  the ledger this directory records to
    python ledger.py configure --repo <dir> --ledger <file> [--visibility <word>] [--lang ko|en]
    python ledger.py pending                                sessions of this repository with unrecorded messages
    python ledger.py extract [--session <id>] [--max-chars N]   candidate bundle after the cursor (JSON)
    python ledger.py append --session <id> --to <line> < entries.md   assign IDs, append, move the cursor
    python ledger.py skip --session <id> --to <line>        move the cursor without recording
    python ledger.py hook                                   PostToolUse hook: after a git commit, index it and remind

The current session comes from CLAUDE_CODE_SESSION_ID. Transcripts are only read, never changed.
Writes go to the ledger, the config file, and the state directory (cursors and the commit index).
Values that look like secrets are masked in every excerpt and refused in appended entries.
Override locations with WORK_LEDGER_CONFIG, WORK_LEDGER_STATE, and WORK_LEDGER_TRANSCRIPTS.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys
from pathlib import Path

CONFIG = Path(os.environ.get("WORK_LEDGER_CONFIG", "~/.config/work-ledger/config.json")).expanduser()
STATE = Path(os.environ.get("WORK_LEDGER_STATE", "~/.local/state/work-ledger")).expanduser()
TRANSCRIPTS = Path(os.environ.get("WORK_LEDGER_TRANSCRIPTS", "~/.claude/projects")).expanduser()

# --- masking -----------------------------------------------------------------------------------

KEY_FORMATS = {
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----|$)"),
    "anthropic-key": re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
    "openai-key": re.compile(r"\bsk-(?:(?:proj|svcacct|admin)-[A-Za-z0-9_\-]{20,}|[A-Za-z0-9]{32,})"),
    "stripe-key": re.compile(r"\b[sr]k_live_[A-Za-z0-9]{20,}"),
    "toss-key": re.compile(r"\blive_g?sk_[A-Za-z0-9]{16,}"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "slack-token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "bearer": re.compile(r"(?i)(?<=bearer )[A-Za-z0-9._~+/=-]{16,}"),
    "url-password": re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"),
    # Addresses identify people and employers; a GitHub noreply address is public by design.
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@(?!users\.noreply\.github\.com\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
}
# The value after a secret-named key: PASSWORD=x, "api_key": "x", or a flag such as --token x.
SECRET_NAME = r"(?:[\w.-]*(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?key|token|credential)[\w.-]*|[\w.-]*_key\b)"
SECRET_ASSIGN = re.compile(
    rf"(?i)((?:{SECRET_NAME}['\"]?\s*[:=]|--{SECRET_NAME}\s)\s*['\"]?)([^'\"\s,;&|()]{{6,}})(?![^'\"\s,;&|)])")
# A literal passed to a call on the right of a secret name (password=hash("x")), or a default after a
# secret-named lookup (getenv("DB_PASSWORD", "x")): the literal is the value.
SECRET_LITERAL = re.compile(
    rf"(?i)((?:{SECRET_NAME}\s*[:=]\s*[\w.]+\(\s*|['\"]{SECRET_NAME}['\"]\s*,\s*)(['\"]))([^'\"]+)(?=\2)")
# References, identifiers, and plain numbers (a token count is not a token).
NOT_SECRET = re.compile(
    r"(?i)^(\$\{?[A-Z_]|<|true$|false$|null$|none$|[a-z]+(?:[_.][a-z]+)+$|[\d,._:-]+[a-z]?$|os\.environ|process\.env|getenv)")


def mask(text: str) -> tuple[str, int]:
    count = 0

    def sub(kind):
        def repl(m):
            nonlocal count
            count += 1
            return f"<masked:{kind}>"
        return repl

    for kind, rx in KEY_FORMATS.items():
        text = rx.sub(sub(kind), text)

    def assign(m):
        nonlocal count
        if NOT_SECRET.match(m.group(2)) or m.group(2).startswith("<masked"):
            return m.group(0)
        count += 1
        return m.group(1) + "<masked:secret-value>"

    def literal(m):
        nonlocal count
        if m.group(3).startswith("<masked"):
            return m.group(0)
        count += 1
        return m.group(1) + "<masked:secret-value>"

    text = SECRET_LITERAL.sub(literal, text)
    return SECRET_ASSIGN.sub(assign, text), count


# --- config and state --------------------------------------------------------------------------

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def real(p: str) -> str:
    return os.path.realpath(os.path.expanduser(p))


def resolve(cwd: str) -> dict | None:
    """The configured entry whose repo contains cwd (longest match), or None."""
    cwd = real(cwd)
    best = None
    for e in load_json(CONFIG, {}).get("ledgers", []):
        repo = real(e["repo"])
        if (cwd == repo or cwd.startswith(repo + os.sep)) and (best is None or len(repo) > len(real(best["repo"]))):
            best = e
    if best:
        best = dict(best, ledger=real(best["ledger"]))
    return best


def cursors() -> dict:
    return load_json(STATE / "cursors.json", {})


def set_cursor(session: str, line: int) -> None:
    """Move the cursor, and remember the message IDs passed over: a continued session's transcript repeats
    messages of the session it continues, with the same IDs, and those must not be offered twice."""
    c = cursors()
    path = find_transcript(session)
    start = (c.get(session) or {}).get("line", 0)
    if line <= start:   # never move back: the range before it was already recorded or skipped
        return
    if path:
        ids = []
        for n, d in records(path, start):
            if n > line:
                break
            if d.get("uuid"):
                ids.append(d["uuid"][:12])
        STATE.mkdir(parents=True, exist_ok=True)
        with (STATE / "seen.txt").open("a", encoding="utf-8") as f:
            f.write("".join(i + "\n" for i in ids))
    c[session] = {"line": line, "updated": now()}
    save_json(STATE / "cursors.json", c)


def seen_ids() -> set[str]:
    try:
        return set((STATE / "seen.txt").read_text(encoding="utf-8").split())
    except OSError:
        return set()


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


# --- transcripts -------------------------------------------------------------------------------

def find_transcript(session: str) -> Path | None:
    hits = glob.glob(str(TRANSCRIPTS / "*" / f"{session}.jsonl"))
    return Path(hits[0]) if hits else None


def records(path: Path, start: int = 0):
    """(line number from 1, record) for every line after `start`."""
    with path.open(encoding="utf-8", errors="replace") as f:
        for n, raw in enumerate(f, 1):
            if n <= start:
                continue
            try:
                yield n, json.loads(raw)
            except ValueError:
                continue


def session_cwd(path: Path) -> str | None:
    for n, d in records(path):
        if d.get("cwd"):
            return d["cwd"]
        if n > 200:
            return None
    return None


def title_of(path: Path) -> str | None:
    """The last AI title, read from the end of the file only."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 400_000))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    titles = re.findall(r'"aiTitle":"((?:[^"\\]|\\.)*)"', tail)
    return json.loads(f'"{titles[-1]}"') if titles else None


TAGS = re.compile(r"<(system-reminder|local-command-stdout|local-command-stderr|command-message)>[\s\S]*?</\1>")
COMMAND = re.compile(r"<command-name>([^<]*)</command-name>")
COMMAND_ARGS = re.compile(r"<command-args>([^<]*)</command-args>")


def user_text(d: dict) -> str | None:
    """What the person typed, or None for tool results, injected context, and summaries."""
    if d.get("type") != "user" or d.get("isMeta") or d.get("isCompactSummary") or d.get("isSidechain"):
        return None
    c = (d.get("message") or {}).get("content")
    if isinstance(c, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
            return None
        c = "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(c, str):
        return None
    m = COMMAND.search(c) if c.lstrip().startswith("<command-") else None
    if m:
        a = COMMAND_ARGS.search(c)
        return f"{m.group(1).strip()} {(a.group(1) if a else '').strip()}".strip()
    c = TAGS.sub("", c).strip()
    if c.startswith(("<task-notification>", "<local-command-caveat>")):   # injected by the harness, not typed
        return None
    return c or None


def blocks(d: dict, kind: str) -> list[dict]:
    c = (d.get("message") or {}).get("content")
    return [b for b in c if isinstance(b, dict) and b.get("type") == kind] if isinstance(c, list) else []


def result_text(b: dict) -> str:
    c = b.get("content")
    if isinstance(c, list):
        c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
    return c if isinstance(c, str) else ""


def clip(text: str, n: int, tail: bool = False) -> str:
    text = text.strip()
    if len(text) <= n:
        return text
    return "…" + text[-n:] if tail else text[:n] + "…"


# Commands whose outcome is usually worth a look: irreversible git and file operations, model runs, and runs of
# tests, evaluations, benchmarks, training, or deploys. Checked per segment of a command line, never on tools whose
# runs are not results (`cat evals/x` is a read, not an eval), and never inside heredoc bodies.
QUIET = {"cat", "head", "tail", "sed", "grep", "rg", "ls", "find", "wc", "echo", "printf", "diff", "stat", "du",
         "awk", "jq", "sort", "uniq", "cut", "tr", "nl", "file", "which", "cd", "pwd", "[", "true", "mkdir", "ln",
         "cp", "mv", "touch", "export", "time", "env", "xargs", "tee", "basename", "dirname", "realpath", "open",
         "chmod", "source", ".", "for", "do", "done", "if", "then", "fi", "while"}
RISKY = re.compile(
    r"(?i)\bgit\s+(?:-C\s+\S+\s+)?(?:commit|push|reset\s+--hard|rebase|tag|filter-repo|branch\s+-D|clean\s+-[a-z]*f)\b|"
    r"\brm\s+-[a-z]*r[a-z]*f\s+(?!\S*(?:tmp|temp|scratch|cache|__pycache__|node_modules|\.venv|\bbuild|\bdist)\b)|"
    r"\bclaude\s+-p\b|\bcodex\s+exec\b")
# Not "test": a passing unit test run is not worth a ledger entry, and a failing one already shows as an error.
RUN_WORD = re.compile(r"(?i)eval|bench|train|score|deploy|release|migrat")
# Programs that run their first arguments: their script or target name says what ran (`python3 score_run.py`).
RUNNERS = {"python", "python3", "node", "bash", "sh", "zsh", "npm", "pnpm", "yarn", "npx", "bun", "deno", "make",
           "just", "uv", "poetry", "cargo", "go", "ruby", "gradle", "gradlew", "mvn", "docker", "kubectl", "terraform"}
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1([^\n]*)\n[\s\S]*?\n\s*\2(?=\s|$)")
# Lines that carry a measurement: a fraction, a percentage, money, a duration, a multiplier, or a token count.
METRIC = re.compile(
    r"\d+(?:\.\d+)?\s*/\s*\d+|\d+(?:\.\d+)?\s*%|\$\s?\d|\d\s*(?:ms|s|초|분|시간)\b|\d+(?:\.\d+)?\s*(?:x|배)\b|"
    r"\d[\d,.]*\s*(?:tokens?|토큰|k\b|M\b)")


def without_heredocs(cmd: str) -> str:
    return HEREDOC.sub(lambda m: "<<" + m.group(2) + m.group(3) + " …", cmd)


def notable(cmd: str) -> bool:
    cmd = without_heredocs(cmd)
    for seg in re.split(r"&&|\|\||;|\||\n", cmd):
        words = seg.strip().split()
        while words and "=" in words[0] and not words[0].startswith("-"):   # VAR=value prefixes
            words = words[1:]
        if not words or os.path.basename(words[0]) in QUIET:
            continue
        if RISKY.search(" ".join(words)):
            return True
        # The program and the first arguments by file name: `python3 tools/score_run.py`, `npm run test`,
        # `./deploy.sh`, `python -m unittest`. A path that merely contains a word (skills/deploy-check/x.py) is not a run.
        prog = os.path.basename(words[0])
        names = [prog] + ([w for w in words[1:3] if not w.startswith(("-", "'", '"'))] if prog in RUNNERS else [])
        if any(RUN_WORD.search(os.path.splitext(os.path.basename(w))[0]) for w in names):
            return True
    return False


PROPOSAL = re.compile(r"(?i)추천|권장|제안|recommend|suggest|propos")
SELF = re.compile(
    r"(?i)빈틈|원인은|원인이었|잘못|틀렸|누락|버그|실패했|되지 않았|고치겠습니다|고쳤습니다|고쳐서|고친 |"
    r"\bbug\b|\bwrong\b|mistake|root cause|the cause|regressed|did not work|didn't work|\bleak|\bfixed\b")
COMMIT_LINE = re.compile(r"^\[(?P<branch>[^\]\s]+)(?: \(root-commit\))? (?P<sha>[0-9a-f]{7,40})\] (?P<subject>.*)$", re.M)


def extract(path: Path, start: int, max_chars: int) -> dict:
    session = path.stem
    sid = session[:8]
    signals: list[dict] = []
    size = 0
    # State that spans records: the reply a short message answers, commands still waiting for their result,
    # and the last error (to collapse retries). A bundle that ends mid-turn saves it for the next one.
    carried = (load_json(STATE / "resume.json", {}).get(session) or {}).get(str(start)) or {}
    last_text = carried.get("last_text", "")    # assistant text since the last thing the person typed
    tools: dict[str, dict] = carried.get("tools", {})
    last_error = carried.get("last_error", {})
    meta = {"models": set(), "branches": set(), "files_changed": [], "commits": [], "first": None, "last": None}
    usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    seen_messages: set[str] = set()
    seen = seen_ids()
    repeated = 0
    to_line = start
    complete = True
    total_lines = start
    masked = 0

    def add(sig: dict) -> None:
        nonlocal masked
        for k in ("text", "context", "command", "result"):
            if isinstance(sig.get(k), str):
                sig[k], n = mask(sig[k])
                masked += n
        if isinstance(sig.get("lines"), list):
            out = []
            for line in sig["lines"]:
                line, n = mask(line)
                masked += n
                out.append(line)
            sig["lines"] = out
        batch.append(sig)

    for n, d in records(path, start):
        total_lines = n
        if not complete:
            continue
        # A soft cap checked between records, so a record is either fully in this bundle or left for the next.
        if signals and size >= max_chars:
            complete = False
            continue
        if (d.get("uuid") or "")[:12] in seen:   # already recorded or skipped in another session's transcript
            repeated += 1
            to_line = n
            continue
        ref = f"{sid}#{(d.get('uuid') or '')[:8]}"
        ts = d.get("timestamp")
        if ts:
            meta["first"] = meta["first"] or ts
            meta["last"] = ts
        if d.get("gitBranch"):
            meta["branches"].add(d["gitBranch"])
        batch: list[dict] = []
        t = d.get("type")

        if t == "assistant":
            m = d.get("message") or {}
            if m.get("model") and not m["model"].startswith("<"):
                meta["models"].add(m["model"])
            mid = m.get("id")
            if mid and mid not in seen_messages and m.get("usage"):
                seen_messages.add(mid)
                u = m["usage"]
                usage["input"] += u.get("input_tokens") or 0
                usage["output"] += u.get("output_tokens") or 0
                usage["cache_read"] += u.get("cache_read_input_tokens") or 0
                usage["cache_write"] += u.get("cache_creation_input_tokens") or 0
            for b in blocks(d, "text"):
                text = b.get("text", "")
                last_text += "\n" + text
                rows = [l.strip() for l in text.splitlines()]
                lines = [l for l in rows if METRIC.search(l) and len(l) > 3]
                if len(lines) >= 2:
                    sig = {"ref": ref, "time": ts, "kind": "numbers", "lines": [clip(l, 200) for l in lines[:8]]}
                    # The sentence before the first number usually says what was measured and on what.
                    first = rows.index(lines[0])
                    lead = [l for l in rows[:first] if l and not l.startswith("|")][-1:]
                    if lead:
                        sig["lead"] = clip(lead[0], 240)
                    add(sig)
                # What the agent itself found wrong ("the cause was", "this leaked", "I'll fix") never reaches a
                # user message, so it would be lost; keep those lines.
                own = [l for l in rows if SELF.search(l) and not l.startswith("|")]
                if own:
                    add({"ref": ref, "time": ts, "kind": "self", "lines": [clip(l, 240) for l in own[:3]]})
            for b in blocks(d, "tool_use"):
                inp = b.get("input") or {}
                tools[b.get("id")] = {"name": b.get("name"), "input": inp}
                fp = inp.get("file_path") or inp.get("notebook_path")
                if b.get("name") in ("Write", "Edit", "NotebookEdit") and fp and fp not in meta["files_changed"]:
                    meta["files_changed"].append(fp)

        elif t == "user":
            for b in blocks(d, "tool_result"):
                use = tools.pop(b.get("tool_use_id"), None) or {}
                cmd = (use.get("input") or {}).get("command", "") if use.get("name") == "Bash" else ""
                out = result_text(b)
                if cmd and "commit" in cmd:
                    for c in COMMIT_LINE.finditer(out):
                        meta["commits"].append(f"{c['sha'][:7]} {c['subject'][:80]}")
                if b.get("is_error") and use.get("name") == "Bash":   # a failed click or edit is not a result
                    desc = clip(cmd or json.dumps(use.get("input") or {}, ensure_ascii=False), 200)
                    # A retry loop of the same failing command is one signal.
                    if last_error.get("command") != mask(desc)[0]:
                        add({"ref": ref, "time": ts, "kind": "error", "tool": use.get("name"),
                             "command": desc, "result": clip(out, 200)})
                        last_error = {"command": batch[-1]["command"]}
                elif cmd and notable(cmd):
                    add({"ref": ref, "time": ts, "kind": "command", "command": clip(without_heredocs(cmd), 300),
                         "result": clip(out, 300, tail=True)})
            text = user_text(d)
            if text is not None:
                if text.startswith("[Request interrupted"):
                    add({"ref": ref, "time": ts, "kind": "interrupt", "context": clip(last_text, 300, tail=True)})
                else:
                    sig = {"ref": ref, "time": ts, "kind": "user", "text": clip(text, 600)}
                    # A short reply ("go with the recommendation") only means something next to what it answers:
                    # the end of that reply, and the lines that made a recommendation, wherever they were.
                    if len(text) < 120 and last_text.strip():
                        tail = clip(last_text, 400, tail=True)
                        picks = [clip(l.strip(), 200) for l in last_text.splitlines()
                                 if PROPOSAL.search(l) and l.strip()[:60] not in tail][-2:]
                        sig["context"] = "\n".join(picks + ["…"] if picks else []) + tail
                    add(sig)
                last_text = ""

        if any(sig["kind"] != "error" for sig in batch):
            last_error = {}   # only consecutive failures of the same command are one signal
        for sig in batch:
            signals.append(sig)
            size += len(json.dumps(sig, ensure_ascii=False))
        to_line = n

    # Keyed by the line the next bundle starts from; a few recent ones per session are enough.
    resume = load_json(STATE / "resume.json", {})
    states = resume.get(session) or {}
    states[str(to_line)] = {"last_text": last_text[-20000:], "tools": dict(list(tools.items())[-50:]),
                            "last_error": last_error}
    resume[session] = dict(list(states.items())[-5:])
    try:
        save_json(STATE / "resume.json", resume)
    except OSError:
        pass
    meta["models"] = sorted(meta["models"])
    meta["branches"] = sorted(meta["branches"])
    meta["files_changed"] = meta["files_changed"][:40]
    return {
        "session": session,
        "transcript": str(path),
        "title": title_of(path),
        "cwd": session_cwd(path),
        "range": {"from_line": start + 1, "to_line": to_line, "complete": complete,
                  "remaining_lines": total_lines - to_line},
        "meta": dict(meta, usage=usage),
        "masked_values": masked,
        "skipped_repeated": repeated,
        "size_chars": size,
        "signals": signals,
    }


# --- ledger ------------------------------------------------------------------------------------

HEADERS = {
    "ko": """# 작업 장부

> work-ledger가 덧붙이기만 하는 기록이다. 지난 항목은 고치지 않는다. 바뀐 내용은 새 항목으로 남기고 "<ID>를 대체"라고 적는다.
>
> 읽는 법(이 장부로 보고서, 이력, 블로그를 쓰는 세션에게):
> - 제목 줄은 `ID · 종류 · 공개 범위`다. ID 글자는 D 결정, M 측정, C 정정, F 실패다.
> - 공개 범위를 지킨다. `사내`는 회사 밖 문서에 쓰지 않는다. `개인`은 익명화한 뒤에만 공개 문서에 쓴다.
> - `[확인 필요]`가 붙은 값은 사실로 쓰지 않는다. `[기억 날짜]`는 사람이 나중에 기억으로 보충한 값이다.
> - 숫자를 인용할 때는 항목의 조건(모델, 횟수, 채점자 등)을 함께 쓴다.
> - 출처 `xxxxxxxx#yyyyyyyy`는 Claude Code 세션 ID 앞 8자리와 메시지 ID 앞 8자리다.
""",
    "en": """# Work ledger

> Append-only record kept by work-ledger. Old entries are never edited; a change is a new entry that says "supersedes <ID>".
>
> How to read it (for a session that writes reports, resumes, or posts from this ledger):
> - The title line is `ID · kind · visibility`. ID letters: D decision, M measurement, C correction, F failure.
> - Respect visibility: `internal` never leaves the company; `private` goes into public writing only anonymized.
> - Never state a value marked `[needs check]` as fact. `[memory <date>]` is a value a person added later from memory.
> - Quote a number together with its conditions (model, runs, scorer, ...).
> - A source `xxxxxxxx#yyyyyyyy` is the first 8 characters of a Claude Code session ID and a message ID.
""",
}
ENTRY_TITLE = re.compile(r"^## \{([DMCF])\} · \S.* · \S.*$")
SOURCED = re.compile(
    r"\b[0-9a-f]{8}#[0-9a-f]{8}\b|\b(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b|[\w./-]+\.\w+:\d+|\[(?:확인 필요|needs check|기억|memory)")


def split_entries(text: str) -> list[str]:
    parts = re.split(r"(?m)^(?=## )", text.strip())
    return [p.strip() for p in parts if p.strip()]


def next_ids(ledger: Path, date: str) -> dict[str, int]:
    used = {k: 0 for k in "DMCF"}
    if ledger.exists():
        for kind, num in re.findall(rf"(?m)^## {date}-([DMCF])(\d+)\b", ledger.read_text(encoding="utf-8")):
            used[kind] = max(used[kind], int(num))
    return used


def cmd_append(args, cfg) -> int:
    entries = split_entries(sys.stdin.read())
    if not entries:
        return fail("no entries on stdin")
    problems = []
    for i, e in enumerate(entries, 1):
        lines = e.splitlines()
        if not ENTRY_TITLE.match(lines[0]):
            problems.append(f"entry {i}: title must be '## {{D|M|C|F}} · <kind> · <visibility>'")
        if not 3 <= len(lines) <= 7:
            problems.append(f"entry {i}: {len(lines)} lines; keep the title plus 2 to 6 lines")
        if not SOURCED.search(e):
            problems.append(f"entry {i}: no source (session#message, path:line, commit, or [확인 필요])")
        if mask(e)[1]:
            problems.append(f"entry {i}: contains a value that looks like a secret")
    if problems:
        return fail("; ".join(problems))
    if args.check:
        print(json.dumps({"ok": True, "entries": len(entries)}))
        return 0
    session = current_session(args)
    if not session or args.to is None:
        return fail("append needs --to <line> and a session (--session or CLAUDE_CODE_SESSION_ID), or --check")
    ledger = Path(cfg["ledger"])
    date = dt.date.today().isoformat()
    used = next_ids(ledger, date)
    out, ids = [], []
    for e in entries:
        kind = ENTRY_TITLE.match(e.splitlines()[0]).group(1)
        used[kind] += 1
        eid = f"{date}-{kind}{used[kind]}"
        ids.append(eid)
        out.append(e.replace("{" + kind + "}", eid, 1))
    ledger.parent.mkdir(parents=True, exist_ok=True)
    new = not ledger.exists() or ledger.stat().st_size == 0
    with ledger.open("a", encoding="utf-8") as f:
        if new:
            f.write(HEADERS.get(cfg.get("lang", "en"), HEADERS["en"]))
        f.write("\n" + "\n\n".join(out) + "\n")
    set_cursor(session, args.to)
    print(json.dumps({"ledger": str(ledger), "appended": ids, "cursor": args.to}, ensure_ascii=False))
    return 0


# --- commands ----------------------------------------------------------------------------------

def fail(msg: str) -> int:
    print(json.dumps({"error": msg}, ensure_ascii=False))
    return 1


def current_session(args) -> str | None:
    return getattr(args, "session", None) or os.environ.get("CLAUDE_CODE_SESSION_ID")


def cmd_pending(cfg) -> int:
    repo = real(cfg["repo"])
    cur = cursors()
    rows = []
    for f in glob.glob(str(TRANSCRIPTS / "*" / "*.jsonl")):
        p = Path(f)
        cwd = session_cwd(p)
        if not cwd or not (real(cwd) == repo or real(cwd).startswith(repo + os.sep)):
            continue
        start = (cur.get(p.stem) or {}).get("line", 0)
        typed, last = 0, None
        for _, d in records(p, start):
            if user_text(d) is not None:
                typed += 1
                last = d.get("timestamp") or last
        if typed:
            rows.append({"session": p.stem, "title": title_of(p), "unrecorded_messages": typed,
                         "last": last, "recorded_up_to_line": start})
    rows.sort(key=lambda r: r["last"] or "", reverse=True)
    print(json.dumps({"repo": repo, "sessions": rows[:20]}, ensure_ascii=False, indent=1))
    return 0


def cmd_hook() -> int:
    """PostToolUse: index a successful git commit and print a one-line reminder. Never blocks the session."""
    try:
        p = json.load(sys.stdin)
        if p.get("tool_name") != "Bash":
            return 0
        cmd = (p.get("tool_input") or {}).get("command", "")
        if not re.search(r"\bgit\s+(?:-C\s+\S+\s+)?commit\b", cmd):
            return 0
        resp = p.get("tool_response")
        out = resp.get("stdout", "") if isinstance(resp, dict) else str(resp or "")
        m = COMMIT_LINE.search(out)
        cfg = resolve(p.get("cwd") or os.getcwd())
        if not m or not cfg:
            return 0
        tp = Path(p["transcript_path"]) if p.get("transcript_path") else None
        row = {"time": now(), "session": p.get("session_id"), "transcript": str(tp) if tp else None,
               "cwd": p.get("cwd"), "branch": m["branch"], "sha": m["sha"], "subject": m["subject"],
               "title": title_of(tp) if tp else None, "ledger": cfg["ledger"]}
        STATE.mkdir(parents=True, exist_ok=True)
        with (STATE / "index.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        msg = (f"work-ledger: 커밋 {m['sha'][:7]}을 색인했습니다. 남길 결정이나 측정이 있으면 /work-ledger"
               if cfg.get("lang") == "ko" else
               f"work-ledger: indexed commit {m['sha'][:7]}. Run /work-ledger if there is a decision or result to record.")
        print(json.dumps({"systemMessage": msg}, ensure_ascii=False))
    except Exception:  # a reminder must never break the session
        pass
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("where")
    c = sub.add_parser("configure")
    c.add_argument("--repo", required=True)
    c.add_argument("--ledger", required=True)
    c.add_argument("--visibility", default="private")
    c.add_argument("--lang", default="en", choices=sorted(HEADERS))
    sub.add_parser("pending")
    e = sub.add_parser("extract")
    e.add_argument("--session")
    e.add_argument("--max-chars", type=int, default=40000)
    for name in ("append", "skip"):
        s = sub.add_parser(name)
        s.add_argument("--session")
        s.add_argument("--to", type=int)
    sub.choices["append"].add_argument("--check", action="store_true", help="validate the entries; write nothing")
    sub.add_parser("hook")
    args = ap.parse_args()

    if args.cmd == "hook":
        return cmd_hook()
    if args.cmd == "configure":
        data = load_json(CONFIG, {})
        rows = [r for r in data.get("ledgers", []) if real(r["repo"]) != real(args.repo)]
        rows.append({"repo": real(args.repo), "ledger": real(args.ledger),
                     "visibility": args.visibility, "lang": args.lang})
        save_json(CONFIG, dict(data, ledgers=rows))
        print(json.dumps({"config": str(CONFIG), "ledgers": rows}, ensure_ascii=False, indent=1))
        return 0

    cfg = resolve(os.getcwd())
    if not cfg:
        return fail(f"no ledger configured for {real(os.getcwd())}; ask the user where to keep it, then run configure")
    if args.cmd == "where":
        cfg["state"] = str(STATE)
        cfg["config"] = str(CONFIG)
        print(json.dumps(cfg, ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "pending":
        return cmd_pending(cfg)

    if args.cmd == "append":
        return cmd_append(args, cfg)
    session = current_session(args)
    if not session:
        return fail("no session: pass --session <id> (see pending)")
    if args.cmd == "skip":
        if args.to is None:
            return fail("skip needs --to <line>")
        set_cursor(session, args.to)
        print(json.dumps({"session": session, "cursor": args.to}))
        return 0
    if args.cmd == "append":
        return cmd_append(args, cfg)

    path = find_transcript(session)
    if not path:
        return fail(f"transcript for session {session} not found under {TRANSCRIPTS}")
    start = (cursors().get(session) or {}).get("line", 0)
    bundle = extract(path, start, args.max_chars)
    bundle.update(ledger=cfg["ledger"], visibility=cfg.get("visibility"), lang=cfg.get("lang"))
    print(json.dumps(bundle, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
