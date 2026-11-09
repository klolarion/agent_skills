"""Check a written report against its claim ledger and saved sources, before handing it over.

Usage:
    python check_report.py --report <file> --dir <evidence>

Read-only: it reads the report, ledger.json, and the saved source texts. Headings may be in any language.
The report is read as units: each table row, each list item, and each sentence of a paragraph or blockquote.
A unit's citations ([2], [1, 3]) say which sources back it. The sources list is the lines that start with [n].
Text before the first `##` heading is the title and meta line, and is not checked for facts.

Errors (exit 1): ledger verification errors (see sources.py verify); no citations; a citation with no line in
the sources list; a listed source that is not in the ledger or whose line lacks the ledger URL or label; a
source listed twice; the ledger's as-of date missing from the meta line; in a cited unit, a number that is in no
quote from the cited sources (years: in no cited source text), or a name with a number (H100, S3) or a date
that is in no cited source text. A version is a number: v3.0.0 and 3.0.0 match.
Warnings: a listed source never cited; outside judgment callouts, an uncited unit with a number or date that no
cited unit in the report has; in a cited sentence or table cell, a word in inline code (`legacy-shuffle`) that is in
no cited source text; anywhere, a name with a number that is in no saved source at all.
Single digits, the topic's own numbers and names, and the ledger's `allow` tokens are not checked.
A cited number passes if any quote (or claim conditions) from a cited source has it, not only the claim the
sentence states. The checks see numbers, names with numbers, dates, and code; a fact in plain words passes unseen.
stats.length counts the characters of the body, without whitespace, from the first `##` heading to the sources list.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # importing sources must not write __pycache__ into the skill
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sources import Sources, compact, date_in, exempt, is_year, load, skipped, tokens, verify, years  # noqa: E402

CODE_RE = re.compile(r"`([^`]+)`")
CELL_RE = re.compile(r"(?<!\\)\|")
CODE_MIN = 3  # shorter code words (`id`, `-v`) match almost any source
CITE_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
SOURCE_LINE_RE = re.compile(r"^\s*(?:[-*]\s+)?\[(\d+)\]\s+(\S.*)$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
DELIMITER_RE = re.compile(r"^\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?$")
LABEL_RE = re.compile(r"^\s*\*\*([^*]+?)\*\*")
URL_RE = re.compile(r"https?://\S+")
SENTENCE_END_RE = re.compile(r"[.!?。](?:\s*\[\d+(?:\s*,\s*\d+)*\])*(?=\s)")
JUDGMENT_LABELS = {"판단", "평가", "의견", "제 판단", "제 평가", "judgment", "judgement", "assessment",
                   "our view", "my view"}


def is_judgment(text: str) -> bool:
    label = LABEL_RE.match(text)
    return bool(label) and label.group(1).strip(" :：").lower() in JUDGMENT_LABELS


def split_sentences(text: str) -> list[str]:
    parts, start = [], 0
    for match in SENTENCE_END_RE.finditer(text):
        parts.append(text[start:match.end()].strip())
        start = match.end()
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def read_report(lines: list[str]) -> tuple[int, dict, list[tuple[int, str, bool, bool]]]:
    """(first body line, sources list {n: (line, text)}, units [(line, text, judgment, table row)])."""
    first_body = next((n for n, line in enumerate(lines, 1) if line.startswith("## ")), min(len(lines), 5) + 1)
    listed: dict[int, list] = {}
    units: list[tuple[int, str, bool, bool]] = []
    block: list[str] = []
    block_start, block_kind, fence = 0, "", ""

    def flush() -> None:
        nonlocal block, block_kind
        if block:
            text = " ".join(block)
            judgment = is_judgment(text)
            units.extend((block_start, sentence, judgment, False) for sentence in split_sentences(text))
        block, block_kind = [], ""

    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        marker = FENCE_RE.match(line)
        if fence:
            if stripped.startswith(fence):
                fence = ""
            continue
        if marker:
            flush()
            fence = marker.group(1)
            continue
        source = SOURCE_LINE_RE.match(line)
        if source:
            flush()
            listed.setdefault(int(source.group(1)), []).append((number, source.group(2)))
            continue
        if number < first_body:
            continue
        if not stripped or stripped.startswith("#") or stripped.startswith("<!--"):
            flush()
            continue
        if stripped.startswith("|"):
            flush()
            if not DELIMITER_RE.match(stripped):
                units.append((number, stripped.strip("|"), False, True))
            continue
        if stripped.startswith(">"):
            if block_kind != "quote":
                flush()
                block_kind, block_start = "quote", number
            block.append(stripped.lstrip(">").strip())
            continue
        if LIST_RE.match(line):
            flush()
            block_kind, block_start = "item", number
            block.append(LIST_RE.sub("", line, count=1).strip())
            continue
        if block_kind == "quote":
            flush()
        if not block:
            block_kind, block_start = "para", number
        block.append(stripped)
    flush()
    return first_body, listed, units


def check(report: Path, folder: Path) -> dict:
    ledger = load(folder)
    sources = Sources(folder, ledger)
    errors: list[dict] = []
    warnings: list[dict] = []

    ledger_result = verify(folder, ledger, sources)
    if ledger_result["errors"]:
        errors.append({"problem": f"the ledger has {len(ledger_result['errors'])} errors; "
                       f"run sources.py verify --dir {folder} and fix them first"})

    lines = report.read_text(encoding="utf-8").splitlines()
    first_body, listed, units = read_report(lines)
    end = min((entries[0][0] for entries in listed.values()), default=len(lines) + 1)
    length = len(re.sub(r"\s", "", "\n".join(lines[first_body - 1:end - 1])))

    meta = tokens("\n".join(lines[:first_body - 1]))
    if not date_in(ledger["as_of"], meta["dates"]):
        errors.append({"line": 1, "problem": f"the as-of date {ledger['as_of']} from the ledger is not in the "
                       "title or meta line"})

    for n, entries in listed.items():
        if len(entries) > 1:
            errors.append({"line": entries[1][0], "problem": f"[{n}] is listed more than once"})
        line, text = entries[0]
        entry = sources.entries.get(n)
        if entry is None:
            removed = any(s["id"] == n and s.get("removed") for s in ledger["sources"])
            errors.append({"line": line, "problem": f"[{n}] " + ("was removed from the ledger" if removed
                                                                 else "is not a source in the ledger")})
        elif entry.get("url") and entry["url"] not in text:
            errors.append({"line": line, "problem": f"[{n}] does not show the ledger's URL or label {entry['url']}"})

    skip_names, skip_numbers = skipped(ledger)

    # Numbers a cited unit may use: those in quotes and conditions of claims from the cited sources, and
    # the stated numbers of derived claims built from them.
    claims = {c.get("id"): c for c in ledger["claims"]}
    quoted: dict[int, set] = {n: set() for n in sources.entries}
    for claim in claims.values():
        if "derived_from" in claim:
            inputs = {claims[i].get("source") for i in claim["derived_from"] if i in claims}
            for n in inputs & quoted.keys():
                quoted[n].update(tokens(claim.get("claim", ""))["numbers"])
        elif claim.get("source") in quoted:
            quoted[claim["source"]].update(tokens(claim.get("quote", ""))["numbers"])
            quoted[claim["source"]].update(tokens(claim.get("conditions", ""))["numbers"])

    parsed = []
    for line, text, judgment, table in units:
        cites = [int(n) for match in CITE_RE.finditer(text) for n in re.split(r"\s*,\s*", match.group(1))]
        found = tokens(URL_RE.sub(" ", CITE_RE.sub(" ", text)).replace("`", ""))
        parsed.append((line, text, judgment, table, cites, found))
    # a number already stated with a citation may be repeated without one (in a limit, the conclusion)
    cited_tokens = {t for *_, cites, found in parsed if cites for t in found["numbers"] + found["dates"]}

    cited_all: set[int] = set()
    stats = {"units": len(units), "cited_units": 0, "citations": 0, "numbers_checked": 0, "names_checked": 0,
             "code_checked": 0, "length": length}
    for line, text, judgment, table, cites, found in parsed:
        names = [name for name in dict.fromkeys(found["names"]) if name not in skip_names]
        numbers = [n for n in dict.fromkeys(found["numbers"]) if not exempt(n) and n not in skip_numbers]
        dates = list(dict.fromkeys(found["dates"]))
        excerpt = text if len(text) <= 160 else text[:157] + "..."

        for name in names:
            if not any(sources.has_name(n, name) for n in sources.entries):
                warnings.append({"line": line, "problem": f"{name!r} is in no saved source", "text": excerpt})

        if not cites:
            fresh = [t for t in numbers + dates if t not in cited_tokens]
            if not judgment and fresh:
                warnings.append({"line": line, "problem": f"uncited: {', '.join(fresh)}", "text": excerpt})
            continue

        stats["cited_units"] += 1
        stats["citations"] += len(cites)
        cited_all.update(cites)
        missing = [n for n in cites if n not in listed]
        if missing:
            errors.append({"line": line, "problem": f"cited {missing} but the sources list has no such line",
                           "text": excerpt})
        backing = [n for n in cites if n in sources.entries]
        label = ", ".join(str(n) for n in cites)
        for name in names:
            stats["names_checked"] += 1
            if not any(sources.has_name(n, name) for n in backing):
                elsewhere = [n for n in sources.entries if sources.has_name(n, name)]
                errors.append({"line": line, "problem": f"{name!r} is not in cited source [{label}]"
                               + (f"; it is in [{', '.join(map(str, elsewhere))}]" if elsewhere else ""),
                               "text": excerpt})
        for number in numbers:
            stats["numbers_checked"] += 1
            pools = [years(sources.tokens[n]) if is_year(number) else quoted[n] for n in backing]
            if not any(number in pool for pool in pools):
                elsewhere = [n for n in sources.entries if number in quoted[n]]
                where = "text" if is_year(number) else "ledger quote"
                errors.append({"line": line, "problem": f"{number} is in no {where} from cited source [{label}]"
                               + (f"; a quote from [{', '.join(map(str, elsewhere))}] has it" if elsewhere else ""),
                               "text": excerpt})
        for date in dates:
            if not any(date_in(date, sources.tokens[n]["dates"]) for n in backing):
                errors.append({"line": line, "problem": f"date {date} is not in cited source [{label}]",
                               "text": excerpt})
        if judgment:
            continue
        # in a table, only the cells that carry a citation state facts; "how to check" cells are the writer's
        checked = " ".join(cell for cell in CELL_RE.split(text) if CITE_RE.search(cell)) if table else text
        words = dict.fromkeys(w for span in CODE_RE.findall(checked) for w in span.split() if len(compact(w)) >= CODE_MIN)
        for word in words:
            stats["code_checked"] += 1
            if not any(sources.has_name(n, word) for n in backing):
                elsewhere = [n for n in sources.entries if sources.has_name(n, word)]
                warnings.append({"line": line, "problem": f"`{word}` is not in cited source [{label}]"
                                 + (f"; it is in [{', '.join(map(str, elsewhere))}]" if elsewhere
                                    else "; if it is your own suggestion, keep it out of cited sentences"),
                                 "text": excerpt})

    if not cited_all:
        errors.append({"problem": "the report cites no sources"})
    for n, entries in listed.items():
        if n not in cited_all:
            warnings.append({"line": entries[0][0], "problem": f"[{n}] is listed but never cited"})

    stats.update({"sources_listed": len(listed), "sources_in_ledger": len(sources.entries),
                  "claims": ledger_result["claims"], "claims_verified": ledger_result["verified"]})
    return {"report": str(report), "errors": errors, "warnings": warnings, "stats": stats}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", required=True)
    parser.add_argument("--dir", required=True, help="the evidence folder with ledger.json")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    result = check(Path(args.report), Path(args.dir))
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
