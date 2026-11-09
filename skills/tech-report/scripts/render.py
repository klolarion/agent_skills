"""Render a tech-report Markdown file as one self-contained HTML page.

Usage:
    python render.py --report <file.md> [--out <file.html>]

Handles the Markdown the report uses: headings, paragraphs, lists (two levels), tables, blockquotes and
callouts (a bold label first: 판단/Judgment, 다만/However, 참고/Note), fenced code, `layers` and `flow`
diagram blocks, links, bold, italics, inline code, and citations such as [2], linked to the sources list.
All text is escaped and the page loads nothing from the network; light and dark mode follow the system.
Writes <report>.html next to the report unless --out is given, and never overwrites a file it did not write.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

GENERATOR = "tech-report render.py"
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+")
SOURCE_LINE_RE = re.compile(r"^\s*(?:[-*]\s+)?\[(\d+)\]\s+(\S.*)$")
DELIMITER_RE = re.compile(r"^\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
RULE_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
LABEL_RE = re.compile(r"^\s*\*\*([^*]+?)\*\*")
PIPE_RE = re.compile(r"(?<!\\)\|")
LABELS = {
    "judgment": {"판단", "평가", "의견", "제 판단", "제 평가", "judgment", "judgement", "assessment", "our view", "my view"},
    "caveat": {"다만", "주의", "한계", "however", "caveat", "limit", "limits", "warning"},
    "note": {"참고", "note"},
}

CSS = """
:root { --bg: #fbfaf7; --fg: #1f2023; --muted: #63666c; --line: #e2dfd8; --panel: #f2f0ea; --accent: #2b59c3;
  --judgment-bg: #edf2fd; --judgment: #2b59c3; --caveat-bg: #fdf4e3; --caveat: #b06d06; --note-bg: #eef4ef; --note: #3f7350; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #15161a; --fg: #e7e5e1; --muted: #a2a5ab; --line: #2d2f35; --panel: #1e2025; --accent: #8eaefc;
    --judgment-bg: #1a2336; --judgment: #8eaefc; --caveat-bg: #2c2415; --caveat: #e0a84e; --note-bg: #19261d; --note: #7fb08d; }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--bg); color: var(--fg); word-break: keep-all; overflow-wrap: anywhere;
  font: 16px/1.75 -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", Pretendard, "Noto Sans KR",
  "Malgun Gothic", "Segoe UI", Roboto, sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 56px 16px 96px; }
h1 { font-size: 2rem; line-height: 1.3; letter-spacing: -0.015em; margin: 0 0 14px; }
h2 { font-size: 1.4rem; line-height: 1.4; margin: 60px 0 18px; padding-top: 28px; border-top: 1px solid var(--line); }
h3 { font-size: 1.12rem; margin: 36px 0 10px; }
h4 { font-size: 1rem; margin: 28px 0 8px; color: var(--muted); }
p { margin: 0 0 16px; }
p.meta { color: var(--muted); font-size: .92rem; padding-bottom: 28px; border-bottom: 1px solid var(--line); }
p.caption { color: var(--muted); font-size: .88rem; margin-top: -4px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
ul, ol { padding-left: 1.4em; margin: 0 0 16px; }
li { margin: 4px 0; }
li > ul, li > ol { margin: 4px 0 0; }
code { font: .88em/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; background: var(--panel);
  padding: 1px 5px; border-radius: 4px; }
pre { background: var(--panel); padding: 14px 16px; border-radius: 8px; overflow-x: auto; }
pre code { background: none; padding: 0; }
hr { border: 0; border-top: 1px solid var(--line); margin: 32px 0; }
.table-wrap { overflow-x: auto; margin: 0 0 20px; }
table { width: 100%; border-collapse: collapse; font-size: .93rem; }
th, td { text-align: left; vertical-align: top; padding: 9px 12px; border-bottom: 1px solid var(--line); }
th { font-weight: 600; background: var(--panel); white-space: nowrap; }
blockquote { margin: 0 0 18px; padding: 2px 18px; border-left: 3px solid var(--line); color: var(--muted); }
aside.callout { margin: 0 0 20px; padding: 14px 18px; border-radius: 8px; border-left: 4px solid; }
aside.callout p:last-child { margin-bottom: 0; }
aside.callout > p:first-child > strong:first-child { display: inline-block; margin-right: 6px; font-size: .82rem;
  letter-spacing: .02em; padding: 0 8px; border-radius: 999px; color: var(--bg); }
aside.judgment { background: var(--judgment-bg); border-color: var(--judgment); }
aside.judgment > p:first-child > strong:first-child { background: var(--judgment); }
aside.caveat { background: var(--caveat-bg); border-color: var(--caveat); }
aside.caveat > p:first-child > strong:first-child { background: var(--caveat); }
aside.note { background: var(--note-bg); border-color: var(--note); }
aside.note > p:first-child > strong:first-child { background: var(--note); }
figure { margin: 8px 0 12px; }
figure.layers { display: flex; flex-direction: column; gap: 6px; }
.layer { border: 1px solid var(--line); background: var(--panel); border-radius: 8px; padding: 10px 14px; text-align: center; }
.layer strong, .step strong { display: block; }
.layer span, .step span { color: var(--muted); font-size: .88rem; }
figure.flow { display: flex; flex-wrap: wrap; align-items: stretch; gap: 8px; }
.step { flex: 1 1 140px; border: 1px solid var(--line); background: var(--panel); border-radius: 8px; padding: 10px 14px; text-align: center; }
.arrow { align-self: center; color: var(--muted); }
sup.cite { font-size: .72em; line-height: 0; white-space: nowrap; }
sup.cite a { padding: 0 1px; }
ol.sources { list-style: none; padding: 0; font-size: .9rem; }
ol.sources li { padding: 6px 0; border-bottom: 1px solid var(--line); }
ol.sources .num { color: var(--muted); margin-right: 6px; }
ol.sources li:target { background: var(--judgment-bg); }
@media (max-width: 600px) { main { padding-top: 32px; } h1 { font-size: 1.6rem; } .arrow { transform: rotate(90deg); flex-basis: 100%; text-align: center; } }
"""


def inline(text: str) -> str:
    codes: list[str] = []

    def keep(match):
        codes.append(match.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", keep, text)
    text = html.escape(text, quote=False)

    def link(match):
        label, url = match.group(1), match.group(2).replace('"', "%22")
        if not re.match(r"^(https?:|mailto:|#|[^:]*$)", url, re.I):
            return label
        return f'<a href="{url}">{label}</a>'

    text = re.sub(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)", link, text)
    text = re.sub(r'(?<![="\w>])(https?://[^\s<]+?)(?=[.,;:)\]]*(?:\s|$|<))', r'<a href="\1">\1</a>', text)

    def cite(match):
        numbers = re.split(r"\s*,\s*", match.group(1))
        return '<sup class="cite">[' + ", ".join(f'<a href="#src-{n}">{n}</a>' for n in numbers) + "]</sup>"

    text = re.sub(r"\[(\d+(?:\s*,\s*\d+)*)\]", cite, text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*(?![\s*])(.+?)(?<![\s*])\*(?![*\w])", r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{html.escape(codes[int(m.group(1))], quote=False)}</code>", text)


def label_kind(text: str) -> str | None:
    label = LABEL_RE.match(text)
    if not label:
        return None
    word = label.group(1).strip(" :：").lower()
    return next((kind for kind, words in LABELS.items() if word in words), None)


def cells(row: str) -> list[str]:
    row = row.strip()
    parts = PIPE_RE.split(row)
    if row.startswith("|"):
        parts = parts[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        parts = parts[:-1]
    return [part.strip().replace("\\|", "|") for part in parts]


def table(rows: list[str]) -> str:
    aligns = []
    for cell in cells(rows[1]):
        aligns.append("center" if cell.startswith(":") and cell.endswith(":") else "right" if cell.endswith(":") else "")

    def row_html(row: str, tag: str) -> str:
        out = []
        for i, cell in enumerate(cells(row)):
            style = f' style="text-align:{aligns[i]}"' if i < len(aligns) and aligns[i] else ""
            out.append(f"<{tag}{style}>{inline(cell)}</{tag}>")
        return "<tr>" + "".join(out) + "</tr>"

    head = row_html(rows[0], "th")
    body = "".join(row_html(row, "td") for row in rows[2:])
    return f'<div class="table-wrap"><table><thead>{head}</thead><tbody>{body}</tbody></table></div>'


def diagram(kind: str, body: list[str]) -> str:
    boxes = []
    for line in body:
        if not line.strip():
            continue
        title, _, detail = line.partition("|")
        span = f"<span>{inline(detail.strip())}</span>" if detail.strip() else ""
        boxes.append(f'<div class="{"layer" if kind == "layers" else "step"}"><strong>{inline(title.strip())}</strong>{span}</div>')
    if kind == "flow":
        return '<figure class="flow">' + '<span class="arrow" aria-hidden="true">→</span>'.join(boxes) + "</figure>"
    return '<figure class="layers">' + "".join(boxes) + "</figure>"


def indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def starts_block(lines: list[str], i: int) -> bool:
    line, stripped = lines[i], lines[i].strip()
    if not stripped:
        return True
    if FENCE_RE.match(line) or HEADING_RE.match(stripped) or RULE_RE.match(stripped) or stripped.startswith(">"):
        return True
    if LIST_RE.match(line) or SOURCE_LINE_RE.match(line):
        return True
    return stripped.startswith("|") and i + 1 < len(lines) and bool(DELIMITER_RE.match(lines[i + 1].strip()))


def list_block(lines: list[str], i: int) -> tuple[str, int]:
    first = LIST_RE.match(lines[i])
    base = indent(lines[i])
    ordered = first.group(2)[0].isdigit()
    items: list[list] = []  # [text, child lines]
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            following = i + 1
            if following < len(lines) and lines[following].strip() and indent(lines[following]) >= base and (
                    LIST_RE.match(lines[following]) or indent(lines[following]) > base):
                i += 1
                continue
            break
        marker = LIST_RE.match(line)
        if marker and indent(line) <= base + 1:
            if marker.group(2)[0].isdigit() != ordered:
                break
            items.append([line[marker.end():].strip(), []])
        elif indent(line) > base + 1 and items:
            items[-1][1].append(line)
        elif not marker and items and not starts_block(lines, i):
            items[-1][0] += " " + line.strip()
        else:
            break
        i += 1
    parts = []
    for text, children in items:
        nested = ""
        if children:
            cut = min(indent(c) for c in children if c.strip())
            nested = "".join(blocks([c[cut:] for c in children]))
        parts.append(f"<li>{inline(text)}{nested}</li>")
    tag = "ol" if ordered else "ul"
    start = re.match(r"\s*(\d+)", first.group(2)) if ordered else None
    attr = f' start="{start.group(1)}"' if start and start.group(1) != "1" else ""
    return f"<{tag}{attr}>" + "".join(parts) + f"</{tag}>", i


def blocks(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        line, stripped = lines[i], lines[i].strip()
        if not stripped:
            i += 1
            continue
        fence = FENCE_RE.match(line)
        if fence:
            info = stripped[len(fence.group(1)):].strip().lower()
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence.group(1)):
                body.append(lines[i])
                i += 1
            i += 1
            if info in ("layers", "flow"):
                out.append(diagram(info, body))
            else:
                out.append(f"<pre><code>{html.escape(chr(10).join(body), quote=False)}</code></pre>")
            continue
        heading = HEADING_RE.match(stripped)
        if heading:
            level = len(heading.group(1))
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            i += 1
            continue
        if RULE_RE.match(stripped):
            out.append("<hr>")
            i += 1
            continue
        if stripped.startswith("|") and i + 1 < len(lines) and DELIMITER_RE.match(lines[i + 1].strip()):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            out.append(table(rows))
            continue
        if stripped.startswith(">"):
            quoted = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quoted.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            kind = label_kind("\n".join(quoted).strip())
            inner = "".join(blocks(quoted))
            out.append(f'<aside class="callout {kind}">{inner}</aside>' if kind else f"<blockquote>{inner}</blockquote>")
            continue
        if SOURCE_LINE_RE.match(line):
            items = []
            while i < len(lines) and SOURCE_LINE_RE.match(lines[i]):
                match = SOURCE_LINE_RE.match(lines[i])
                items.append(f'<li id="src-{match.group(1)}"><span class="num">[{match.group(1)}]</span>{inline(match.group(2))}</li>')
                i += 1
            out.append('<ol class="sources">' + "".join(items) + "</ol>")
            continue
        if LIST_RE.match(line):
            rendered, i = list_block(lines, i)
            out.append(rendered)
            continue
        paragraph = [stripped]
        i += 1
        while i < len(lines) and not starts_block(lines, i):
            paragraph.append(lines[i].strip())
            i += 1
        text = " ".join(paragraph)
        if re.fullmatch(r"\*[^*].*\*", text) and not text.startswith("**"):
            out.append(f'<p class="caption">{inline(text)}</p>')
        else:
            out.append(f"<p>{inline(text)}</p>")
    return out


def render(markdown: str) -> str:
    parts = blocks(markdown.splitlines())
    if len(parts) > 1 and parts[0].startswith("<h1>") and parts[1].startswith("<p>"):
        parts[1] = '<p class="meta">' + parts[1][3:]
    title_match = re.search(r"^#\s+(.+)$", markdown, re.M)
    title = re.sub(r"[*_`]", "", title_match.group(1)).strip() if title_match else "Report"
    lang = "ko" if re.search(r"[가-힣]", markdown) else "en"
    body = "\n".join(parts)
    return (f'<!doctype html>\n<html lang="{lang}">\n<head>\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<meta name="generator" content="{GENERATOR}">\n<title>{html.escape(title)}</title>\n'
            f"<style>{CSS}</style>\n</head>\n<body>\n<main>\n{body}\n</main>\n</body>\n</html>\n")


def output_path(report: Path, out: str | None) -> Path:
    path = Path(out) if out else report.with_suffix(".html")
    candidate, n = path, 1
    while candidate.exists() and GENERATOR not in candidate.read_text(encoding="utf-8", errors="replace")[:2000]:
        n += 1
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
    return candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    report = Path(args.report)
    page = render(report.read_text(encoding="utf-8"))
    path = output_path(report, args.out)
    path.write_text(page, encoding="utf-8")
    print(json.dumps({"html": str(path), "bytes": len(page.encode("utf-8"))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
