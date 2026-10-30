"""Check a written report file against the selection and the repository, before handing it over.

Usage:
    python select_items.py <same profile args> | python check_report.py --report <file> --repo <path> --selection -
    python check_report.py --report <file> --selection selection.json [--repo <path>]

Read-only like probes.py: it reads the report, the selection output, and tracked files of the repository.
Headings may be in any language; tables are recognized by their shape:
    All items      ID in the first column, seven or more columns, a level column and a verdict column
    Gap            ID first, a level column and a verdict column, fewer than seven columns
    Blockers       ID first, a verdict column, no level column
    Fixes          a number in the first column, item IDs in the last column

Errors (exit 1): All items rows that do not match the selection (count, missing, extra, duplicate, level),
a blockers or gap table that does not match the verdicts, a gap item not in exactly one fix, a gap row
pointing to a fix that does not exist, a table row whose cell count differs from its header (an unescaped `|`),
a shortened path, a line beyond the end of the file.
Warnings: a cited path that is not a tracked file, a cited line in a file too large to read (not checked),
a verdict word that could not be read.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probes import MAX_FILE_BYTES, Repo  # noqa: E402

LEVELS = {"L1": 1, "L2": 2, "L3": 3}
ID_RE = re.compile(r"\b[A-Z]+(?:-[A-Z0-9]+)*-\d{2}\b")
ID_FULL = re.compile(r"^[A-Z]+(?:-[A-Z0-9]+)*-\d{2}$")
LEVEL_FULL = re.compile(r"^L[1-3]$")
VERDICTS = [  # prefixes, lower case; English first, then the Korean words reports have used
    ("needs confirmation", "confirm"), ("확인 필요", "confirm"), ("n/a", "na"), ("해당 없음", "na"),
    ("partial", "partial"), ("부분", "partial"), ("pass", "pass"), ("통과", "pass"), ("fail", "fail"), ("실패", "fail"),
]
DELIMITER_RE = re.compile(r"^\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?$")  # | --- | :-: | (outer pipes optional)
PIPE_RE = re.compile(r"(?<!\\)\|")
PATH_RE = re.compile(
    r"(?<![\w/.@-])((?:[\w.@-]+/)*(?:[\w-][\w.-]*\.[A-Za-z]\w{0,9}|Dockerfile[\w.-]*|Makefile|Jenkinsfile|Procfile))"
    r":(\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*)")


def clean(cell: str) -> str:
    return cell.strip().strip("`*_ ").strip()


def verdict_of(cell: str) -> str | None:
    text = re.sub(r"^[\W\d_]+", "", clean(cell).lower())  # "✅ Pass" reads as "pass"
    for prefix, verdict in VERDICTS:
        if text.startswith(prefix):
            return verdict
    return None


def tables(text: str, errors: list | None = None) -> list[list[list[str]]]:
    """Markdown tables as lists of rows (header and separator dropped).

    A row with more or fewer cells than its header is reported: a `|` inside a cell (even in
    backticks, as in `|| true`) splits it, and the table renders broken.
    """
    found, current = [], []
    lines = text.splitlines() + [""]
    for n, line in enumerate(lines):
        stripped = line.strip()
        # GFM makes the outer pipes optional; a row without them starts a table only above a delimiter row.
        if stripped.startswith("|") or (PIPE_RE.search(stripped) and (current or (
                PIPE_RE.search(lines[n + 1]) and DELIMITER_RE.match(lines[n + 1].strip())))):
            lead = stripped.startswith("|")
            trail = stripped.endswith("|") and not stripped.endswith("\\|")
            current.append(PIPE_RE.split(stripped)[1 if lead else 0:-1 if trail else None])
            continue
        if len(current) > 2:
            width = len(current[0])
            for row in current[2:]:
                if row and len(row) != width and errors is not None:
                    errors.append(f"table row has {len(row)} cells, header has {width} (escape | inside a cell as \\|): "
                                  f"{'|'.join(row)[:80].strip()}")
            found.append([r for r in current[2:] if r])
        current = []
    return found


def column(rows: list[list[str]], test) -> int | None:
    """Index of the first column where most rows pass the test."""
    width = min(len(r) for r in rows)
    for i in range(1, width):
        if sum(1 for r in rows if test(r[i])) * 2 > len(rows):
            return i
    return None


def classify(found: list[list[list[str]]]) -> dict:
    out: dict = {"all": None, "gap": None, "blockers": None, "fixes": None}
    for rows in found:
        if not rows:
            continue
        if (all(re.fullmatch(r"\d+", clean(r[0])) for r in rows) and min(len(r) for r in rows) >= 3
                and all(ID_RE.search(r[-1]) for r in rows)):
            out["fixes"] = out["fixes"] or rows
            continue
        id_rows = [r for r in rows if ID_FULL.match(clean(r[0]))]
        if len(id_rows) * 2 <= len(rows) or min(len(r) for r in id_rows) < 3:
            continue
        level = column(id_rows, lambda c: bool(LEVEL_FULL.match(clean(c))))
        verdict = column(id_rows, lambda c: verdict_of(c) is not None)
        if verdict is None:
            continue
        entry = {"rows": id_rows, "level": level, "verdict": verdict}
        if min(len(r) for r in id_rows) >= 7 and level is not None:
            if out["all"] is None or len(id_rows) > len(out["all"]["rows"]):
                out["all"] = entry
        elif level is not None:
            out["gap"] = out["gap"] or entry
        else:
            out["blockers"] = out["blockers"] or entry
    return out


def listed(errors: list, what: str, ids: set[str]) -> None:
    if ids:
        errors.append(f"{what}: {', '.join(sorted(ids))}")


def check_tables(text: str, selection: dict, errors: list, warnings: list) -> dict:
    selected = {i["id"]: i for i in selection["items"]}
    excluded = {i["id"]: i for i in selection["excluded"]}
    expected = {**selected, **excluded}
    target = LEVELS.get(selection.get("target_level", "L3"), 3)
    t = classify(tables(text, errors))
    if t["all"] is None:
        errors.append("no \"All items\" table found (ID first, seven or more columns, level and verdict columns)")
        return {}

    rows = t["all"]["rows"]
    ids = [clean(r[0]) for r in rows]
    counts = Counter(ids)
    if len(rows) != len(expected):
        errors.append(f"All items has {len(rows)} rows; selection has {len(selected)} selected + {len(excluded)} excluded = {len(expected)}")
    listed(errors, "in the selection but not in All items", set(expected) - set(ids))
    listed(errors, "in All items but not in the selection", set(ids) - set(expected))
    listed(errors, "more than one row in All items", {i for i, n in counts.items() if n > 1})

    verdicts = {}
    for r in rows:
        i = clean(r[0])
        level = clean(r[t["all"]["level"]])
        if i in expected and level != expected[i]["level"]:
            errors.append(f"{i}: level {level} in All items, {expected[i]['level']} in the pack")
        v = verdict_of(r[t["all"]["verdict"]])
        if v is None:
            warnings.append(f"{i}: verdict \"{clean(r[t['all']['verdict']])}\" not recognized; blockers and gap checks skip it")
        verdicts[i] = v

    def failing(i: str) -> bool:
        return verdicts.get(i) in ("fail", "partial")

    blockers_shown: set[str] = set()
    if t["blockers"] is not None:
        blockers_shown = {clean(r[0]) for r in t["blockers"]["rows"]}
        should = {i for i, item in selected.items() if item.get("blocker") and failing(i)}
        listed(errors, "failed or partial blockers missing from the blockers table", should - blockers_shown)
        listed(errors, "in the blockers table but not a failed or partial blocker in All items", blockers_shown - should)

    gap_ids: set[str] = set()
    if t["gap"] is not None:
        gap_rows = t["gap"]["rows"]
        gap_ids = {clean(r[0]) for r in gap_rows}
        should = {i for i, item in selected.items() if failing(i) and LEVELS[item["level"]] <= target}
        # A gap table may leave out blockers already shown in the blockers table.
        listed(errors, "Fail or Partial at or below the target but missing from the gap table (a report file has every row)",
               should - gap_ids - blockers_shown)
        listed(errors, "in the gap table but not Fail or Partial at or below the target", gap_ids - should)
        gap_ids |= should & blockers_shown
    else:
        warnings.append("no gap table found; gap and fix checks skipped")

    if t["fixes"] is not None and gap_ids:
        numbers = {clean(r[0]) for r in t["fixes"]}
        closes = Counter(i for r in t["fixes"] for i in set(ID_RE.findall(r[-1])))
        listed(errors, "gap items in no fix row", {i for i in gap_ids if closes[i] == 0})
        listed(errors, "gap items in more than one fix row; each belongs to exactly one", {i for i in gap_ids if closes[i] > 1})
        for r in t["gap"]["rows"]:
            ref = clean(r[-1])
            if re.fullmatch(r"\d+", ref) and ref not in numbers:
                errors.append(f"{clean(r[0])}: gap row points to fix {ref}, which does not exist")
    elif gap_ids:
        warnings.append("no fixes table found; fix checks skipped")

    return {"all_items_rows": len(rows), "gap_rows": len(gap_ids),
            "fix_rows": len(t["fixes"]) if t["fixes"] else 0,
            "blocker_rows": len(t["blockers"]["rows"]) if t["blockers"] else 0}


def check_paths(text: str, repo: Repo, errors: list, warnings: list) -> int:
    tracked = {repo.rel(p): p for p in repo.files}
    lengths: dict[str, int] = {}
    seen = set()
    for m in PATH_RE.finditer(text):
        path, lines = m.group(1), m.group(2)
        if (path, lines) in seen:
            continue
        seen.add((path, lines))
        if "..." in path:
            errors.append(f"{path}:{lines}: shortened path; write the full path")
            continue
        if path not in tracked:
            full = [t for t in tracked if t.endswith("/" + path)]
            if full:
                errors.append(f"{path}:{lines}: shortened path; write the full path ({', '.join(sorted(full)[:2])})")
            else:
                warnings.append(f"{path}:{lines}: not a tracked file")
            continue
        if repo.too_large(tracked[path]):
            warnings.append(f"{path}:{lines}: line range not checked; the file is over {MAX_FILE_BYTES} bytes")
            continue
        if path not in lengths:
            lengths[path] = len(repo.read(tracked[path]).splitlines())
        last = max(int(n) for n in re.findall(r"\d+", lines))
        if last > lengths[path]:
            errors.append(f"{path}:{lines}: line {last} is beyond the end of the file ({lengths[path]} lines)")
    return len(seen)


def selection_problem(selection) -> str | None:
    """What check_tables needs from select_items.py output, or None when it is all there."""
    if not isinstance(selection, dict):
        return f"expected a JSON object, got {type(selection).__name__}"
    for key in ("items", "excluded"):
        if not isinstance(selection.get(key), list):
            return f"'{key}' is missing or not a list"
        for item in selection[key]:
            if not (isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("level") in LEVELS):
                return f"an entry in '{key}' lacks an id or a level L1-L3"
    return None


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Check a written report file (read-only).")
    ap.add_argument("--report", type=Path, required=True, help="report file to check")
    ap.add_argument("--selection", required=True, help="select_items.py output as a file, or - for stdin")
    ap.add_argument("--repo", type=Path, help="audited repository; enables the path checks")
    args = ap.parse_args(argv)
    if not args.report.is_file():
        print(f"error: {args.report} is not a file", file=sys.stderr)
        return 2
    if args.repo is not None and not args.repo.is_dir():
        print(f"error: {args.repo} is not a directory", file=sys.stderr)
        return 2
    try:
        raw = sys.stdin.read() if args.selection == "-" else Path(args.selection).read_text(encoding="utf-8")
        selection = json.loads(raw)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read the selection: {exc}", file=sys.stderr)
        return 2
    problem = selection_problem(selection)
    if problem:
        print(f"error: the selection is not select_items.py output: {problem}", file=sys.stderr)
        return 2

    text = args.report.read_text(encoding="utf-8", errors="ignore")
    errors: list[str] = []
    warnings: list[str] = []
    counts = check_tables(text, selection, errors, warnings)
    if args.repo is not None:
        repo = Repo(args.repo)
        counts["paths_checked"] = check_paths(text, repo, errors, warnings)
        counts["path_source"] = repo.source
    json.dump({"ok": not errors, "errors": errors, "warnings": warnings, "counts": counts},
              sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
