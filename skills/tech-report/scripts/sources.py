"""Save the text of a report's sources, and check the claim ledger against them.

Usage:
    python sources.py init --dir <evidence> --topic <subject> [--reader <text>] [--as-of YYYY-MM-DD]
    python sources.py fetch <url> --dir <evidence> [--title <t>] [--kind primary|secondary]
    python sources.py add <file|-> --dir <evidence> --url <url or label> [--title <t>] [--kind ...] [--via user|agent]
    python sources.py remove <n> --dir <evidence> --reason <why>
    python sources.py peek <url> [--find <text> ...]
    python sources.py claims <file|-> --dir <evidence>
    python sources.py verify --dir <evidence>

The evidence folder holds ledger.json and sources/<n>.txt. init creates the ledger; fetch and add save a
source's text under the next number (or the number it already has) and record it. fetch refuses a bot check
or challenge page saved in place of the document. remove deletes a source's text and keeps its entry as a
removed record, so its number is never reused; it refuses while a claim uses it. peek prints a page's text
(or the lines that contain --find terms) without saving anything, to decide whether a page is worth a source.
claims replaces the ledger's claims with a JSON list from a file and runs verify.
verify checks every claim against the saved text:
  - the quote appears in its source (case, whitespace, quote marks, dashes, and Markdown markup ignored;
    "..." inside a quote skips text, and the parts must appear in order),
  - every number in the claim appears in the quote; every name with a number (H100, S3), year, and date
    in the claim or its conditions appears in the source; every number in the conditions appears in the source.
    "The source" is its saved text and its URL, and a year may appear there inside a date. A date in the
    conditions may also be the day the source was saved. A version is a number: v3.0.0 and 3.0.0 match.
    The topic's own names and numbers, and the ledger's `allow` tokens, are not checked,
  - a derived claim's inputs are quoted claims, its formula uses only numbers from their quotes, and the
    claim states the formula's value, rounded as written.

Errors (exit 1): any of the above failing, an unknown source, a missing or duplicate claim ID.
Warnings: a saved text under 500 characters (the page may need JavaScript), a saved text that looks like a bot
check, a quote too short to pin its fact, a source saved from the agent's own tool (quotes match what the tool
returned, not necessarily the page).
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import difflib
import json
import operator
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path

LEDGER = "ledger.json"
MAX_BYTES = 8 * 1024 * 1024
SHORT_TEXT = 500
SHORT_QUOTE = 12
BLOCK_CHECK = 5000  # challenge pages are short; a long document that mentions one is not checked
USER_AGENT = "tech-report/0.0.3 (Python urllib)"  # honest: browser-like strings are what bot checks challenge
PEEK_LINES = 40
DEFAULT_ALLOW = ["P0", "P1", "P2", "P3"]
FORMULA_CONSTANTS = {"1", "10", "100", "1000"}

# --- ledger ------------------------------------------------------------------------------------


def new_ledger(topic: str = "", reader: str = "", as_of: str | None = None) -> dict:
    return {"topic": topic, "reader": reader, "as_of": as_of or dt.date.today().isoformat(),
            "allow": list(DEFAULT_ALLOW), "sources": [], "claims": []}


def load(folder: Path) -> dict:
    path = folder / LEDGER
    if not path.exists():
        return new_ledger()
    ledger = json.loads(path.read_text(encoding="utf-8"))
    for key, value in new_ledger().items():
        ledger.setdefault(key, value)
    return ledger


def save(folder: Path, ledger: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / LEDGER).write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_source(folder: Path, source: dict) -> str:
    path = folder / source.get("file", "")
    return path.read_text(encoding="utf-8") if path.is_file() else ""


# --- text extraction ---------------------------------------------------------------------------

BLOCK_TAGS = {"address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "figcaption", "figure",
              "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "ol", "p", "pre",
              "section", "table", "tbody", "thead", "tr", "ul"}
SKIP_TAGS = {"head", "noscript", "script", "style", "svg", "template"}


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self.skip = 0
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        elif tag in SKIP_TAGS:
            self.skip += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        elif tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    parser = _HTMLText()
    parser.feed(html)
    parser.close()
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in "".join(parser.parts).split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text, " ".join(parser.title.split())


def json_to_text(value, path: str = "") -> list[str]:
    """Flatten JSON into `key: value` lines, so quotes from string values (a release body) match as text."""
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("node_id", "gravatar_id", "url") or (key.endswith("_url") and key != "html_url"):
                continue
            lines += json_to_text(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            lines += json_to_text(item, f"{path}[{index}]")
    elif isinstance(value, str) and value.strip():
        lines.append(f"{path}:\n{value}" if "\n" in value else f"{path}: {value}")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        lines.append(f"{path}: {value}")
    return lines


def to_text(body: str, kind: str) -> tuple[str, str]:
    """(text, title) from a page or file body; kind is a content type or a file suffix."""
    head = body.lstrip()[:200].lower()
    if "html" in kind or head.startswith(("<!doctype html", "<html")):
        return html_to_text(body)
    if "json" in kind:
        try:
            data = json.loads(body)
        except ValueError:
            return body, ""
        title = (data.get("name") or data.get("title") or "") if isinstance(data, dict) else ""
        return "\n".join(json_to_text(data)), str(title)
    return body, ""


def fetch(url: str) -> tuple[str, str]:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "text/html,application/json;q=0.9,text/plain;q=0.8,*/*;q=0.5"})
    with urllib.request.urlopen(request, timeout=30) as response:
        content_type = response.headers.get_content_type()
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError(f"larger than {MAX_BYTES // (1024 * 1024)} MB")
    if content_type == "application/pdf" or raw[:5] == b"%PDF-":
        raise ValueError("PDF: convert it to text (for example with pdftotext) and save it with add")
    return to_text(raw.decode(charset, errors="replace"), content_type)


BLOCKED_RE = re.compile(
    r"making sure you['’]re not a bot|protected by anubis|just a moment\.\.\.|checking your browser"
    r"|verify you are (?:a )?human|enable javascript and cookies to continue|attention required! \| cloudflare"
    r"|ddos protection by", re.I)


def looks_blocked(text: str) -> bool:
    """A bot check or challenge page that a site served instead of the document."""
    return len(text) < BLOCK_CHECK and bool(BLOCKED_RE.search(text))


def add_source(folder: Path, ledger: dict, text: str, url: str, title: str, kind: str, via: str) -> tuple[dict, str]:
    existing = next((s for s in ledger["sources"] if s.get("url") == url), None)
    number = existing["id"] if existing else max((s["id"] for s in ledger["sources"]), default=0) + 1
    relative = f"sources/{number}.txt"
    (folder / "sources").mkdir(parents=True, exist_ok=True)
    (folder / relative).write_text(text, encoding="utf-8")
    entry = {"id": number, "title": title or url, "url": url, "kind": kind, "via": via,
             "accessed": dt.date.today().isoformat(), "file": relative, "chars": len(text)}
    if existing:
        ledger["sources"][ledger["sources"].index(existing)] = entry
    else:
        ledger["sources"].append(entry)
    save(folder, ledger)
    return entry, "updated" if existing else "added"


def remove_source(folder: Path, ledger: dict, number: int, reason: str) -> dict:
    """Delete a source's text and keep its entry as a removed record. Raises ValueError if a claim uses it."""
    entry = next((s for s in ledger["sources"] if s["id"] == number and not s.get("removed")), None)
    if entry is None:
        raise ValueError(f"source {number} is not in the ledger")
    users = [c.get("id") for c in ledger["claims"] if c.get("source") == number]
    if users:
        raise ValueError(f"claims {', '.join(map(str, users))} use source {number}; delete or move them first")
    if entry.get("file"):
        (folder / entry["file"]).unlink(missing_ok=True)
    entry.pop("file", None)
    entry.pop("chars", None)
    entry["removed"] = reason
    save(folder, ledger)
    return entry


# --- normalizing and tokens ----------------------------------------------------------------------

_TRANSLATE = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
})
_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MARKUP_RE = re.compile(r"[*_`#>|~\\]")
_ELLIPSIS_RE = re.compile(r"\[?\.{3,}\]?")


def normalize(text: str) -> str:
    """Lower case, NFKC, plain quote marks and dashes, Markdown links and markup removed, whitespace collapsed."""
    text = unicodedata.normalize("NFKC", text).translate(_TRANSLATE)
    text = _LINK_RE.sub(r"\1", text)
    text = _MARKUP_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def compact(text: str) -> str:
    """normalize() without any whitespace: spacing differs between a page, its text, and a quote of it."""
    return re.sub(r"\s+", "", normalize(text))


def quote_parts(quote: str) -> list[str]:
    return [part for part in (compact(p) for p in _ELLIPSIS_RE.split(normalize(quote))) if part]


def find_quote(quote: str, text_compact: str) -> bool:
    parts, position = quote_parts(quote), 0
    for part in parts:
        found = text_compact.find(part, position)
        if found < 0:
            return False
        position = found + len(part)
    return bool(parts)


def nearest(quote: str, text: str) -> str:
    """The stretch of the source that shares the longest run with the quote, to show what it actually says."""
    a, b = normalize(text), normalize(quote)
    if not a or not b:
        return ""
    match = difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    if match.size < 4:
        return ""
    start = max(0, match.a - match.b - 40)
    return a[start:start + len(b) + 80]


_MONTH_NAMES = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MONTH = (r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
          r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_DATE_RES = [  # most specific first; each match is blanked before the next pattern runs
    ("ymd", re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")),
    ("ymd", re.compile(r"(?<!\d)(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")),
    ("mdy", re.compile(_MONTH + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?!\d)", re.I)),
    ("dmy", re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH + r",?\s+(\d{4})(?!\d)", re.I)),
    ("ym", re.compile(r"(?<!\d)(\d{4})\s*년\s*(\d{1,2})\s*월")),
    ("my", re.compile(_MONTH + r",?\s+(\d{4})(?!\d)", re.I)),
]
NAME_RE = re.compile(r"(?<![A-Za-z0-9.])[A-Za-z][A-Za-z_-]*\d+(?:\.\d+)*[A-Za-z]*(?![A-Za-z0-9])")
VERSION_RE = re.compile(r"v(\d+)((?:\.\d+)+)")  # v3.0.0 is the number 3.0.0, not a name
NUMBER_RE = re.compile(r"(?<![A-Za-z0-9.,])(\d{1,3}(?:,\d{3})+|\d+)((?:\.\d+)*)(?!\d)")


def _month(name: str) -> int:
    return _MONTH_NAMES.index(name[:3].lower()) + 1


def _date(kind: str, groups: tuple) -> str | None:
    if kind == "ymd":
        year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
    elif kind == "mdy":
        year, month, day = int(groups[2]), _month(groups[0]), int(groups[1])
    elif kind == "dmy":
        year, month, day = int(groups[2]), _month(groups[1]), int(groups[0])
    elif kind == "ym":
        year, month, day = int(groups[0]), int(groups[1]), 0
    else:
        year, month, day = int(groups[1]), _month(groups[0]), 0
    if not (1 <= month <= 12 and 0 <= day <= 31 and 1900 <= year <= 2199):
        return None
    return f"{year:04d}-{month:02d}" + (f"-{day:02d}" if day else "")


def _number(whole: str, fraction: str) -> str:
    whole = whole.replace(",", "")
    if fraction.count(".") > 1:  # a version such as 3.0.0
        return whole + fraction
    try:
        return format(Decimal(whole + fraction).normalize(), "f")
    except InvalidOperation:
        return whole + fraction


def tokens(text: str) -> dict:
    """Dates (ISO), names with a number in them (lower case), and other numbers, normalized for comparison."""
    text = unicodedata.normalize("NFKC", text).translate(_TRANSLATE)
    dates: list[str] = []
    for kind, pattern in _DATE_RES:
        def blank(match, kind=kind):
            value = _date(kind, match.groups())
            if value is None:
                return match.group(0)
            dates.append(value)
            return " " * len(match.group(0))
        text = pattern.sub(blank, text)
    names: list[str] = []
    numbers: list[str] = []
    for match in NAME_RE.finditer(text):
        version = VERSION_RE.fullmatch(match.group(0).lower())
        if version:
            numbers.append(_number(*version.groups()))
        else:
            names.append(match.group(0).lower())
    text = NAME_RE.sub(" ", text)
    numbers += [_number(match.group(1), match.group(2)) for match in NUMBER_RE.finditer(text)]
    return {"dates": dates, "names": names, "numbers": numbers}


def skipped(ledger: dict) -> tuple[set, set]:
    """Names and numbers not checked as facts: the topic's own (Larkspur 3.0) and the ledger's allow list."""
    allow = [str(t) for t in ledger.get("allow", [])]
    found = tokens(" ".join([ledger.get("topic", "")] + allow))
    return set(found["names"]) | {t.lower() for t in allow}, set(found["numbers"])


def years(found: dict) -> set:
    """Years in a text: four-digit numbers, and the year of every date."""
    return {n for n in found["numbers"] if is_year(n)} | {d[:4] for d in found["dates"]}


def exempt(number: str) -> bool:
    """Single digits ("3 changes", "4 new models") are counts the reader can check by eye."""
    return len(number) == 1


def is_year(number: str) -> bool:
    return len(number) == 4 and number.isdigit() and 1900 <= int(number) <= 2199


def date_in(date: str, dates) -> bool:
    return any(d == date or d.startswith(date + "-") or date.startswith(d + "-") for d in dates)


# --- verification ------------------------------------------------------------------------------

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def evaluate(formula: str) -> float:
    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -walk(node.operand)
        raise ValueError("use only numbers, + - * /, and parentheses")
    return walk(ast.parse(re.sub(r"(?<=\d),(?=\d{3})", "", formula), mode="eval"))


def close(number: str, value: float) -> bool:
    decimals = len(number.split(".")[1]) if "." in number else 0
    try:
        return abs(float(number) - value) <= 0.5 * 10 ** -decimals + 1e-9
    except ValueError:
        return False


class Sources:
    """Saved source texts with their tokens, loaded once."""

    def __init__(self, folder: Path, ledger: dict) -> None:
        self.entries = {s["id"]: s for s in ledger["sources"] if not s.get("removed")}
        self.text = {n: read_source(folder, s) for n, s in self.entries.items()}
        self.compact = {n: compact(t) for n, t in self.text.items()}
        # the URL often carries the version or branch (stable/2.7.x) that the text never repeats
        self.tokens = {n: tokens(t + "\n" + self.entries[n].get("url", "")) for n, t in self.text.items()}

    def has_name(self, n: int, name: str) -> bool:
        return compact(name) in self.compact.get(n, "")


def verify(folder: Path, ledger: dict, sources: Sources | None = None) -> dict:
    sources = sources or Sources(folder, ledger)
    errors: list[dict] = []
    warnings: list[dict] = []
    for n, entry in sources.entries.items():
        if len(sources.text[n]) < SHORT_TEXT:
            warnings.append({"source": n, "problem": f"saved text has {len(sources.text[n])} characters; "
                             "the page may need JavaScript or the text may be cut off"})
        if looks_blocked(sources.text[n]):
            warnings.append({"source": n, "problem": "saved text looks like a bot check, not the document; "
                             "save another copy and remove this one"})
        if entry.get("via") == "agent":
            warnings.append({"source": n, "problem": "text came from the agent's own tool; quotes match that text, "
                             "which may differ from the page"})

    claims: dict[str, dict] = {}
    for index, claim in enumerate(ledger["claims"]):
        cid = claim.get("id")
        if not cid:
            errors.append({"claim": f"#{index + 1}", "problem": "claim has no id"})
        elif cid in claims:
            errors.append({"claim": cid, "problem": "duplicate claim id"})
        else:
            claims[cid] = claim

    skip = skipped(ledger)
    verified = 0
    for cid, claim in claims.items():
        problems = (_check_derived(claim, claims) if "derived_from" in claim
                    else _check_quoted(claim, sources, skip))
        found = [p for p in problems if p.get("level", "error") == "error"]
        for problem in problems:
            level = problem.pop("level", "error")
            (errors if level == "error" else warnings).append({"claim": cid, **problem})
        if not found:
            verified += 1
    return {"sources": len(sources.entries), "claims": len(claims),
            "derived": sum(1 for c in claims.values() if "derived_from" in c),
            "verified": verified, "errors": errors, "warnings": warnings}


def _check_quoted(claim: dict, sources: Sources, skip: tuple[set, set]) -> list[dict]:
    n = claim.get("source")
    if n not in sources.entries:
        return [{"problem": f"source {n!r} is not in the ledger"}]
    quote = claim.get("quote", "")
    if not quote_parts(quote):
        return [{"problem": "quote is empty"}]
    if not find_quote(quote, sources.compact[n]):
        return [{"source": n, "problem": "quote not found in the saved text; reopen the source",
                 "nearest": nearest(quote, sources.text[n])}]
    problems: list[dict] = []
    if len(compact(quote)) < SHORT_QUOTE:
        problems.append({"level": "warning", "source": n, "problem": "quote is too short to pin the fact; "
                         "quote the sentence around it"})
    in_quote = tokens(quote)
    in_source = sources.tokens[n]
    skip_names, skip_numbers = skip
    for field in ("claim", "conditions"):
        found = tokens(claim.get(field, ""))
        for number in found["numbers"]:
            if exempt(number) or number in skip_numbers:
                continue
            if field == "claim" and not is_year(number):
                if number not in in_quote["numbers"]:
                    problems.append({"source": n, "problem": f"{number} in the claim is not in its quote"})
            elif number not in (years(in_source) if is_year(number) else in_source["numbers"]):
                problems.append({"source": n, "problem": f"{number} in the {field} is not in the source"})
        for name in found["names"]:
            if name not in skip_names and not sources.has_name(n, name):
                problems.append({"source": n, "problem": f"{name!r} in the {field} is not in the source"})
        for date in found["dates"]:
            saved_on = field == "conditions" and date == sources.entries[n].get("accessed")
            if not saved_on and not date_in(date, in_source["dates"]):
                problems.append({"source": n, "problem": f"date {date} in the {field} is not in the source"})
    return problems


def _check_derived(claim: dict, claims: dict) -> list[dict]:
    inputs = claim.get("derived_from") or []
    bad = [i for i in inputs if i not in claims or "derived_from" in claims[i]]
    if not inputs or bad:
        return [{"problem": f"derived_from must list quoted claims; not usable: {bad or 'none listed'}"}]
    pool = set()
    for i in inputs:
        pool.update(tokens(claims[i].get("quote", ""))["numbers"])
    formula = claim.get("formula", "")
    try:
        value = evaluate(formula)
    except (ValueError, SyntaxError, ZeroDivisionError) as exc:
        return [{"problem": f"formula {formula!r} does not evaluate: {exc}"}]
    problems = [{"problem": f"{number} in the formula is not in the quotes of {', '.join(inputs)}"}
                for number in tokens(formula)["numbers"] if number not in pool and number not in FORMULA_CONSTANTS]
    stated = tokens(claim.get("claim", ""))["numbers"]
    if not any(close(number, value) for number in stated):
        problems.append({"problem": f"the claim does not state the formula's value ({value:.4g})"})
    problems += [{"problem": f"{number} in the claim is neither the formula's value nor in the input quotes"}
                 for number in stated if not exempt(number) and number not in pool and not close(number, value)]
    return problems


# --- command line ------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init", help="create or update the ledger")
    p.add_argument("--dir", required=True)
    p.add_argument("--topic", required=True)
    p.add_argument("--reader", default=None)
    p.add_argument("--as-of", default=None)
    for name in ("fetch", "add"):
        p = sub.add_parser(name)
        p.add_argument("url" if name == "fetch" else "file")
        p.add_argument("--dir", required=True)
        if name == "add":
            p.add_argument("--url", required=True, help="the page's URL, or a label for a file")
            p.add_argument("--via", choices=["user", "agent"], default="user")
        p.add_argument("--title", default="")
        p.add_argument("--kind", choices=["primary", "secondary"], default="primary")
    p = sub.add_parser("remove", help="drop a source saved by mistake; its number is not reused")
    p.add_argument("number", type=int)
    p.add_argument("--dir", required=True)
    p.add_argument("--reason", required=True)
    p = sub.add_parser("peek", help="print a page's text without saving it")
    p.add_argument("url")
    p.add_argument("--find", action="append", default=[], help="print only lines with this text (repeatable)")
    p = sub.add_parser("claims", help="replace the ledger's claims with a JSON list, then verify")
    p.add_argument("file")
    p.add_argument("--dir", required=True)
    p = sub.add_parser("verify")
    p.add_argument("--dir", required=True)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.command == "peek":
        try:
            text, title = fetch(args.url)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=1))
            return 1
        result = {"url": args.url, "title": title, "chars": len(text), "bot_check": looks_blocked(text)}
        lines = text.splitlines()
        if args.find:
            terms = [t.lower() for t in args.find]
            hits = [f"{i}: {line}" for i, line in enumerate(lines, 1) if any(t in line.lower() for t in terms)]
            result.update(matches=len(hits), lines=hits[:PEEK_LINES])
        else:
            result["lines"] = lines[:PEEK_LINES]
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0

    folder = Path(args.dir)
    ledger = load(folder)
    code = 0
    if args.command == "init":
        ledger["topic"] = args.topic
        if args.reader is not None:
            ledger["reader"] = args.reader
        if args.as_of:
            ledger["as_of"] = args.as_of
        save(folder, ledger)
        result = {"ledger": str(folder / LEDGER), "topic": ledger["topic"], "reader": ledger["reader"],
                  "as_of": ledger["as_of"], "sources": len(ledger["sources"]), "claims": len(ledger["claims"])}
    elif args.command in ("fetch", "add"):
        try:
            if args.command == "fetch":
                text, title = fetch(args.url)
                url, via = args.url, "fetch"
            else:
                body = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8", errors="replace")
                text, title = to_text(body, Path(args.file).suffix.lower() if args.file != "-" else "")
                url, via = args.url, args.via
        except (OSError, ValueError, urllib.error.URLError) as exc:
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}",
                              "hint": "look for another copy first (the raw file in the project's repository, an API, "
                                      "a mirror); otherwise get the text with your own tool and save it with add "
                                      "--via agent"},
                             ensure_ascii=False, indent=1))
            return 1
        if args.command == "fetch" and looks_blocked(text):
            print(json.dumps({"error": "the site served a bot check instead of the page; nothing was saved",
                              "start": text[:200],
                              "hint": "find another copy (the raw file in the project's repository, an API, a mirror), "
                                      "or get the text with your own tool and save it with add --via agent"},
                             ensure_ascii=False, indent=1))
            return 1
        entry, status = add_source(folder, ledger, text, url, args.title or title, args.kind, via)
        result = {"status": status, **entry, "start": text[:300]}
        if looks_blocked(text):
            result["warning"] = "the text looks like a bot check, not the document; check it before relying on it"
        elif len(text) < SHORT_TEXT:
            result["warning"] = "short text: the page may need JavaScript; check it before relying on it"
    elif args.command == "remove":
        try:
            result = {"status": "removed", **remove_source(folder, ledger, args.number, args.reason)}
        except ValueError as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=1))
            return 1
    elif args.command == "claims":
        try:
            body = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
            data = json.loads(body)
        except (OSError, ValueError) as exc:
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=1))
            return 1
        claims = data.get("claims") if isinstance(data, dict) else data
        if not isinstance(claims, list) or not all(isinstance(c, dict) for c in claims):
            print(json.dumps({"error": "expected a JSON list of claims, or an object with a claims list"},
                             ensure_ascii=False, indent=1))
            return 1
        ledger["claims"] = claims
        save(folder, ledger)
        result = verify(folder, ledger)
        code = 1 if result["errors"] else 0
    else:
        result = verify(folder, ledger)
        code = 1 if result["errors"] else 0
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())
