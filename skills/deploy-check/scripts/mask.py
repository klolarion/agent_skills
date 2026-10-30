"""Print repository lines with secret values masked, so an audit can read them without leaking them.

Usage:
    python mask.py --repo <path> <file>[:<from>-<to>] ...     print those lines, numbered
    python mask.py --repo <path> --grep <regex> [--in <dir>] [--context N]   search tracked files, print matches

Read-only and static like probes.py. Use it for every read of the audited repository: code stays readable,
and a single-token string value is replaced by a description of it:
    "<str len=9 placeholder>"        length and kind, never the characters
Literals with spaces (SQL, messages, prompts), routes, and URLs are shown, with values inside them masked
(key=value pairs for secret-named keys, Bearer tokens, URL passwords, key formats, long random segments).
Kinds: empty, placeholder (change-me, example, test, dummy ...), key format (stripe-key, aws-access-key ...),
high-entropy or low-entropy. Also masked: known key formats anywhere on the line, passwords in URLs,
values of secret-named keys (PASSWORD=..., api_key: ..., --password x, <password>x</password>, the rest of
the line in config files), defaults inside references (${DB_PASSWORD:-x}), the right-hand side of an
assignment to a secret-named variable and the argument of a secret-named setter (a derived password is a
value too), every value in .env files, YAML block values and multi-line strings after a secret name, and
private key blocks.
Shown as they are: environment variable names in lookups (getenv("JWT_SECRET")), references to where a
secret lives (${DB_PASSWORD}, a secret-manager ARN), and identifiers.

It masks; it does not judge. A value written in prose or a comment without a key name can still show.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probes import IDENTIFIER, MAX_FILE_BYTES, PLACEHOLDER, REFERENCE_BLOCK, SECRET_PATTERNS, SECRET_REFERENCE, Repo  # noqa: E402

KEY_FORMATS = {k: v for k, v in SECRET_PATTERNS.items() if k != "generic-assignment"}
STRING = re.compile(r"(?P<q>[\"'`])(?P<body>(?:\\.|(?!(?P=q)).)*)(?P=q)")
FAKE = re.compile(r"(?i)(test|fake|dummy|sample|mock|placeholder|example|changeme|change-me|xxx|todo|<[^>]+>)")
REFERENCE = re.compile(r"^(\$\{[^}]+\}|\$[A-Za-z_][A-Za-z0-9_]*|\{\{.*\}\}|%\([^)]+\)s)$")
SECRETISH = r"(?:password|passwd|passphrase|pwd|pw|secret|token|api[_-]?key|private[_-]?key|access[_-]?key|credential|salt)"
SECRET_NAME = re.compile(rf"(?i)(?:^|[^a-z]){SECRETISH}")
# Narrower for expressions: a token or key variable usually holds a computed value the audit must read.
PASSWORDISH = r"(?:password|passwd|passphrase|pwd|pw|secret)"
# Names that hold something about a password, not the password (password_hash, passwordEncoder).
NOT_A_VALUE = re.compile(r"(?i)(hash|encoder|hasher|policy|length|expir|ttl|pattern|regex|field|column|label|url|path|"
                         r"reset|confirm|changed|valid|verify|check|match|required|minutes|seconds|hours|days|count|size|limit|max|min|"
                         r"prefix|header|name|type)")
# Field names, header names, and media types: shown, since they say what a value is, not what it holds.
LABEL = re.compile(r"^([a-z]+(?:[_.][a-z]+)+|[A-Z][a-z]+(?:-[A-Z][a-z]+)+|[a-z]+/[a-z0-9.+-]+)$")
ENV_LOOKUP = re.compile(r"(?:getenv|environ\.get|environ\s*\[|env\.get|process\.env\s*\[|System\.getenv|os\.Getenv|"
                        r"ENV\s*\[|ENV\.fetch|getProperty|\benv|\bconfig|\brequired)\s*\(?\s*$")
CALL_BEFORE = re.compile(r"([\w.$]*)\s*[\(\[]\s*$")
UPPER_SNAKE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
# KEY = value / key: value for a secret-named key, value unquoted (quoted values are masked as literals).
# The value must be the rest of the line (config style), not the start of an expression.
# A literal right after a secret-named key, or passed to a secret-named call, is a value whatever it looks like.
SECRET_CONTEXT = re.compile(
    rf"(?i)(?:{SECRETISH}[\w.-]*['\"]?\]?\s*(?:=>|(?<![=!<>])=(?!=)|:)[^,;{{}}]*"   # through the value's expression
    rf"|{SECRETISH}[\w.$]*\s*(?:===?|!==?)\s*"                                    # compared with a secret
    rf"|{SECRETISH}[\w.-]*['\"]\s*,\s*"                                           # put("password", ...), getenv("X_KEY", ...)
    rf"|(?:{SECRETISH}|hash|hashpw|bcrypt|argon2|scrypt|encode|digest|encrypt|sign|hmac)[\w$.]*\s*\(\s*(?:[^()]*,\s*)?)$")
# Single-word literals that are shown: a dict key, a module name, an argument of a call about names
# (roles, routes, headers, events, tables), and a few well-known non-secret words.
DICT_KEY_AFTER = re.compile(r"^\s*:")
DICT_KEY_BEFORE = re.compile(r"(?:^\s*|[{,]\s*)$")
NAME_KEY = re.compile(r"(?i)\b(role|roles|type|status|kind|method|mode|env|environment|level|scope|plan|tier|state|"
                      r"algorithm|algorithms)['\"]?\s*(?:=|:)\s*\[?\s*$")
IMPORT_BEFORE = re.compile(r"(?:\bfrom|\bimport|\brequire\s*\()\s*$")
NAME_CALL = re.compile(r"(?i)(role|authorit|permission|scope|route|path|mapping|header|event|emit|queue|table|"
                       r"model|column|field|metric|logger|log|getitem|setitem|get|post|put|patch|delete|on)$")
KNOWN_WORDS = {"HS256", "HS384", "HS512", "RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "EdDSA", "none",
               "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "utf-8", "utf8", "UTF-8", "production",
               "development", "staging", "test", "debug", "info", "warn", "warning", "error", "true", "false", "null"}
PATH_LIKE = re.compile(r"^/[\w/.:{}*@$-]*$")
URL_LIKE = re.compile(r"^[a-z][a-z0-9+.-]*://[^\s?#]*$")
INNER_VALUE = re.compile(rf"(?i)(\b[\w.-]*{SECRETISH}[\w.-]*\s*[=:]\s*)([^\s&;,'\"]+)")
AUTH_SCHEME = re.compile(r"(?i)(\b(?:bearer|basic|token)\s+)(?!\$\{|\{)([A-Za-z0-9._~+/=-]{8,})")
# One path or host segment at a time, so ordinary URLs stay readable.
LONG_TOKEN = re.compile(r"[A-Za-z0-9_+=-]{20,}")
UNQUOTED_VALUE = re.compile(
    rf"(?i)(\b([\w.-]*{SECRETISH}[\w.-]*)['\"]?\s*(?<![=!<>:])(?:=|:)(?![=:])\s*)(?![\"'`‹])"
    r"([^\s#;,()\[\]{{}}‹›\"'`]+)(?=[;,]?\s*(?:#.*|//.*)?$)")
TYPE_NAME = re.compile(r"^(?i:str|string|int|integer|bool|boolean|bytes|secretstr|none|null|nil|undefined|true|false|any|object)$")
# A secret-named variable assigned an expression: everything after "=" up to the end of the statement.
ASSIGNED_EXPR = re.compile(
    rf"(?i)(\b(?:[\w$]+\s+)?([\w$.]*{PASSWORDISH}[\w$]*)['\"]?\s*(?<![=!<>:])(?::=|=|:)(?![=:])\s*)([^;,\n]+?)(?=\s+(?:#|//)|\s*[;,]|\s*$)")
TYPE_ANNOTATION = re.compile(
    r"^(?:[A-Z][\w.]*|str|string|int|bool|bytes|number|any|SecretStr)(?:\[.*\]|<.*>)?\??\s*(?:=.*|[),:;{].*)?$")
# A setter or builder call (setPassword(...), .password(...)), up to the last ")" before an optional comment.
SETTER = re.compile(rf"(?i)((?:\bset|\.)([\w$]*{PASSWORDISH}[\w$]*)\s*\()(.+)(\)\s*[;,]?\s*(?://.*|#.*)?)$")
# 6: an empty user name (redis://:pw@host) and "/" inside the password still count.
URL_PASSWORD = re.compile(r"(://[^:/@\s]*:)([^@\s]+)(@)")
# ${NAME:default}, ${NAME:-default}: the name is a reference, the default may be the secret itself.
REF_DEFAULT = re.compile(r"(\$\{([A-Za-z_][\w.-]*)(?::-|:=|:\?|:|-|=))([^}]*)(\})")
# Config files: the rest of the line after a secret-named key, up to a " #" comment (7: also "?=", ":=" and "+=").
CONFIG_VALUE = re.compile(
    rf"(?i)^(\s*(?:-\s*)?(?:export\s+|ENV\s+)?['\"]?([\w.-]*{SECRETISH}[\w.-]*)['\"]?\s*(?:\?=|:=|\+=|=|:|\s)\s*)"
    r"(.*?)(\s+#.*)?$")
YAML_BLOCK = re.compile(rf"(?i)^(\s*)(?:-\s*)?['\"]?[\w.-]*{SECRETISH}[\w.-]*['\"]?\s*:\s*[|>][-+0-9]*\s*(?:#.*)?$")
# 7: secret-named NAME=value anywhere (shell prefixes, kwargs), command-line flags, XML elements.
KV_ANY = re.compile(rf"(?i)(\b[\w.-]*{SECRETISH}[\w.-]*=)(?!=)([^\s'\"‹;&|,()]+)(?!\()")
FLAG_VALUE = re.compile(rf"(?i)((?:^|\s)--?[\w-]*{SECRETISH}[\w-]*(?:=|\s+))([^\s'\"‹]+)")
MYSQL_PASSWORD = re.compile(r"(?i)(\b(?:mysql\w*|mariadb\w*)\b.*?\s-p)(\S+)")
USER_PASSWORD = re.compile(r"((?:\s-u|--user)\s+[^:\s'\"]+:)([^\s'\"]+)")
XML_ELEMENT = re.compile(rf"(?i)(<([\w:.-]*{SECRETISH}[\w:.-]*)(?:\s[^>]*)?>)([^<]+)(</)")
XML_ENTRY = re.compile(rf"(?i)((?:key|name)\s*=\s*[\"'][^\"']*{SECRETISH}[^\"']*[\"'][^>]*>)([^<]+)(<)")
# A quoted value right after a secret-named key, when quotes paired wrongly (an apostrophe earlier on the line).
# netrc and similar: "password <value>" with a space.
SPACED_PASSWORD = re.compile(r"(?i)(\b(?:password|passwd)\s+)([^\s=:'\"‹]+)")
QUOTED_AFTER_KEY = re.compile(rf"(?i)(\b[\w.-]*{SECRETISH}[\w.-]*\s*[=:]\s*['\"])([^'\"\s‹›]+)")
SAFE_EXPR = re.compile(r"^\s*(?:os\.environ|os\.getenv|process\.env|System\.getenv|env\(|ENV\[|settings\.|config\.|"
                       r"getenv|required\(|None|null|nil|undefined|true|false|\$\{)")
ENV_FILE = re.compile(r"(^\.env($|\.)|\.env$)")
ENV_LINE = re.compile(r"^(\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_.]*\s*=\s*)(.*)$")
BEGIN_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----")
END_KEY = re.compile(r"-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----")
CONFIG_SUFFIXES = {".yml", ".yaml", ".properties", ".ini", ".toml", ".conf", ".cfg", ".env", ".tfvars", ".netrc"}
CONFIG_NAMES = {"Makefile", ".netrc", "_netrc", ".npmrc", ".pypirc", ".pgpass"}


def entropy(s: str) -> float:
    counts = Counter(s)
    return -sum(n / len(s) * math.log2(n / len(s)) for n in counts.values()) if s else 0.0


def describe(value: str) -> str:
    if value == "":
        return "empty"
    kinds = [name for name, rx in KEY_FORMATS.items() if rx.search(value)]
    if "${" in value or "{{" in value:
        return f"len={len(value)} template"
    if FAKE.search(value) or PLACEHOLDER.search(value):
        kinds.append("placeholder")
    if not kinds:
        kinds.append("high-entropy" if len(value) >= 16 and entropy(value) >= 3.5 else "low-entropy")
    return f"len={len(value)} {' '.join(kinds)}"


def shown_as_is(value: str, before: str, after: str = "") -> bool:
    """Names and references, which say where a secret lives, not what it is."""
    if REFERENCE.match(value) or SECRET_REFERENCE.match(value) or LABEL.match(value) or IDENTIFIER.match(value):
        return True
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", value):
        return False
    dict_key = (DICT_KEY_AFTER.match(after) and DICT_KEY_BEFORE.search(before)) or (
        after.lstrip().startswith("]") and before.rstrip().endswith("["))
    if value in KNOWN_WORDS or ENV_LOOKUP.search(before) or dict_key or IMPORT_BEFORE.search(before) or NAME_KEY.search(before):
        return True
    call = CALL_BEFORE.search(before)
    if call and NAME_CALL.search(call.group(1)):
        return True
    return bool(UPPER_SNAKE.match(value) and call and not SECRET_NAME.search(call.group(1)))


def guard(text: str) -> str:
    """Mask values inside a literal that is shown: key formats, URL passwords, key=value pairs, long tokens."""
    for name, rx in KEY_FORMATS.items():
        text = rx.sub(f"‹{name}›", text)
    text = URL_PASSWORD.sub(lambda m: f"{m.group(1)}‹masked›{m.group(3)}", text)
    text = INNER_VALUE.sub(lambda m: f"{m.group(1)}‹value {describe(m.group(2))}›", text)
    text = AUTH_SCHEME.sub(lambda m: f"{m.group(1)}‹value {describe(m.group(2))}›", text)
    return LONG_TOKEN.sub(lambda m: m.group(0) if entropy(m.group(0)) < 3.5 else f"‹value {describe(m.group(0))}›", text)


def mask_strings(line: str) -> str:
    out, pos = [], 0
    for m in STRING.finditer(line):
        q, body = m.group("q"), m.group("body")
        before, after = line[:m.start()], line[m.end():]
        out.append(line[pos:m.start()])
        env_name = ENV_LOOKUP.search(before) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", body)
        if SECRET_CONTEXT.search(before) and not env_name:
            out.append(f"{q}‹str {describe(body)}›{q}")
        elif shown_as_is(body, before, after):
            out.append(m.group(0))
        elif re.search(r"\s", body.strip()) or PATH_LIKE.match(body) or URL_LIKE.match(body):
            out.append(f"{q}{guard(body)}{q}")  # prose, SQL, routes, URLs: what the audit reads
        else:
            out.append(f"{q}‹str {describe(body)}›{q}")
        pos = m.end()
    out.append(line[pos:])
    return "".join(out)


def mask_value(m: re.Match) -> str:
    name, value = m.group(2), m.group(3)
    if (NOT_A_VALUE.search(name) or TYPE_NAME.match(value) or REFERENCE.match(value)
            or SECRET_REFERENCE.match(value) or SAFE_EXPR.match(value)):
        return m.group(0)
    return f"{m.group(1)}‹value {describe(value)}›"


def mask_expr(m: re.Match) -> str:
    name, expr = m.group(2), m.group(3).rstrip()
    annotation = m.group(1).rstrip().endswith(":") and TYPE_ANNOTATION.match(expr)
    if (NOT_A_VALUE.search(name) or SAFE_EXPR.match(expr) or expr.startswith("‹") or annotation
            or re.fullmatch(r"[\"'`]‹str [^›]*›[\"'`]", expr)):
        return m.group(0)
    kind = "with quoted literals" if "‹str" in expr else "no quoted literal"
    return f"{m.group(1)}‹expression masked, {kind}›"


def mask_setter(m: re.Match) -> str:
    if NOT_A_VALUE.search(m.group(2)) or SAFE_EXPR.match(m.group(3)):
        return m.group(0)
    return f"{m.group(1)}‹expression masked›{m.group(4)}"


def mask_default(m: re.Match) -> str:
    name, default = m.group(2), m.group(3)
    if not default or (not SECRET_NAME.search(name) and re.fullmatch(r"[\w./:-]{0,40}", default)
                       and not URL_PASSWORD.search(default)):
        return m.group(0)
    return f"{m.group(1)}‹value {describe(default)}›{m.group(4)}"


def mask_config_value(m: re.Match) -> str:
    name, value = m.group(2), m.group(3).strip()
    if (not value or NOT_A_VALUE.search(name) or value[0] in "|>‹" or REFERENCE.match(value)
            or SECRET_REFERENCE.match(value) or re.fullmatch(r"\$\{[^}]*‹[^}]*\}", value)):
        return m.group(0)
    return f"{m.group(1)}‹value {describe(value.strip(chr(34) + chr(39)))}›{m.group(4) or ''}"


def mask_line(line: str, env_file: bool = False, config: bool = False) -> str:
    line = REF_DEFAULT.sub(mask_default, line)
    if env_file and not line.lstrip().startswith("#"):
        m = ENV_LINE.match(line)
        if m:
            value = m.group(2).strip().strip("\"'")
            return line if REFERENCE.match(value) or SECRET_REFERENCE.match(value) else f"{m.group(1)}‹value {describe(value)}›"
    if config and not line.lstrip().startswith("#"):
        line = CONFIG_VALUE.sub(mask_config_value, line)
        line = SPACED_PASSWORD.sub(lambda m: f"{m.group(1)}‹value {describe(m.group(2))}›", line)
    line = XML_ELEMENT.sub(lambda m: f"{m.group(1)}‹value {describe(m.group(3))}›{m.group(4)}", line)
    line = XML_ENTRY.sub(lambda m: f"{m.group(1)}‹value {describe(m.group(2))}›{m.group(3)}", line)
    line = mask_strings(line)  # first, so a key inside a literal is described with its format
    for name, rx in KEY_FORMATS.items():
        line = rx.sub(f"‹{name}›", line)
    line = URL_PASSWORD.sub(lambda m: f"{m.group(1)}‹masked›{m.group(3)}", line)
    line = SETTER.sub(mask_setter, line)
    line = ASSIGNED_EXPR.sub(mask_expr, line)
    line = UNQUOTED_VALUE.sub(mask_value, line)
    for rx in (KV_ANY, FLAG_VALUE, MYSQL_PASSWORD, USER_PASSWORD, QUOTED_AFTER_KEY):
        line = rx.sub(lambda m: m.group(0) if m.group(2).startswith(("$", "‹")) else
                      f"{m.group(1)}‹value {describe(m.group(2))}›", line)
    return line


TEXT_SUFFIXES = {".md", ".markdown", ".rst", ".txt", ".adoc"}
BACKTICK_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".vue", ".svelte"}


def opens_multiline(line: str, env_file: bool, suffix: str = "") -> tuple[int, str] | None:
    """Where a string that runs onto the next lines starts on this line, and its closing delimiter."""
    if env_file:
        m = ENV_LINE.match(line)
        value = m.group(2).lstrip() if m else ""
        if value[:1] in ("'", '"') and value.count(value[0]) == 1:
            return m.start(2) + len(m.group(2)) - len(value), value[0]
        return None
    if suffix in TEXT_SUFFIXES:
        return None
    delims = ('"""', "'''", "`") if suffix in BACKTICK_SUFFIXES else ('"""', "'''")
    for delim in delims:
        if line.count(delim) % 2 == 1:
            return line.rindex(delim), delim
    return None


def mask_lines(lines: list[str], env_file: bool = False, config: bool = False, suffix: str = "") -> list[str]:
    """Mask line by line, carrying what spans lines: key blocks, reference blocks, YAML block values, and
    strings that run onto later lines (masked whole after a secret name, otherwise masked line by line)."""
    out, in_key, ref_indent, block_indent, open_str = [], False, None, None, None
    for line in lines:
        indent = len(line) - len(line.lstrip())
        if open_str is not None:
            delim, secret = open_str
            if delim in line:
                open_str = None
            out.append(" " * indent + "‹masked›" if secret else mask_line(line, env_file, config))
            continue
        if in_key or BEGIN_KEY.search(line):
            in_key = not END_KEY.search(line)
            out.append("‹private key block›")
            continue
        if block_indent is not None and (not line.strip() or indent > block_indent):
            out.append(" " * indent + "‹masked›" if line.strip() else line)
            continue
        block_indent = None
        if ref_indent is not None and line.strip() and indent <= ref_indent:
            ref_indent = None
        if ref_indent is not None:
            out.append(line)  # CodeBuild "secret-id:json-key" entries name where a secret lives
            continue
        block = REFERENCE_BLOCK.match(line)
        if block:
            ref_indent = len(block.group(1))
        yaml_block = YAML_BLOCK.match(line)
        if yaml_block:
            block_indent = len(yaml_block.group(1))
        start = opens_multiline(line, env_file, suffix)
        if start is not None:
            pos, delim = start
            before = line[:pos]
            secret = env_file or bool(SECRET_CONTEXT.search(before) or SECRET_NAME.search(before.split("=")[0][-60:]))
            open_str = (delim, secret)
            rest = line[pos + len(delim):]
            out.append(mask_line(before, env_file, config) + delim + ("‹masked›" if secret else mask_line(rest, env_file, config)))
            continue
        out.append(mask_line(line, env_file, config))
    return out


def file_kind(name: str) -> tuple[bool, bool, str]:
    """(env file, config file, suffix) by file name."""
    env_file = bool(ENV_FILE.search(name))
    suffix = Path(name).suffix.lower()
    config = env_file or suffix in CONFIG_SUFFIXES or name in CONFIG_NAMES or name.startswith("Dockerfile")
    return env_file, config, suffix


def show(repo: Repo, spec: str) -> list[str]:
    path, _, rng = spec.partition(":")
    p = (repo.root / path).resolve()
    if repo.root not in p.parents or not p.is_file():
        return [f"{spec}: not a file in the repository"]
    if p.stat().st_size > MAX_FILE_BYTES:
        return [f"{spec}: larger than {MAX_FILE_BYTES} bytes; not read (data, not code to audit)"]
    text = repo.read(p)
    if "\0" in text:
        return [f"{spec}: binary file; not shown"]
    lines = text.splitlines()
    first, last = 1, len(lines)
    if rng:
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", rng)
        if not m:
            return [f"{spec}: line range must be <from>-<to>"]
        first, last = int(m.group(1)), int(m.group(2) or m.group(1))
        if last < first:
            return [f"{spec}: line range must be <from>-<to> with from <= to"]
    masked = mask_lines(lines, *file_kind(p.name))
    rel = repo.rel(p)
    return [f"{rel}:{n}: {masked[n - 1]}" for n in range(max(first, 1), min(last, len(lines)) + 1)]


def grep(repo: Repo, pattern: re.Pattern, context: int, scope: list[str], limit: int = 200) -> list[str]:
    out, hits = [], 0
    scope = [Path(s).as_posix().lstrip("./").rstrip("/") if s not in (".", "./") else "" for s in scope]
    for p in repo.files:
        rel = repo.rel(p)
        if scope and not any(s == "" or rel == s or rel.startswith(s + "/") for s in scope):
            continue
        text = repo.read(p)
        if "\0" in text:
            continue  # binary
        lines = text.splitlines()
        rows = [i for i, line in enumerate(lines) if pattern.search(line)]
        if not rows:
            continue
        masked = mask_lines(lines, *file_kind(p.name))
        keep = sorted({j for i in rows for j in range(max(i - context, 0), min(i + context + 1, len(lines)))})
        for j in keep:
            out.append(f"{repo.rel(p)}:{j + 1}: {masked[j]}")
        hits += len(rows)
        if hits >= limit:
            out.append(f"... stopped after {limit} matching lines; narrow the pattern")
            break
    return out or ["no matches"]


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Print repository lines with secret values masked (read-only).")
    ap.add_argument("--repo", type=Path, required=True, help="repository root")
    ap.add_argument("--grep", help="regex to search tracked files for; (?i) at the start ignores case")
    ap.add_argument("--in", dest="scope", action="append", default=[], help="limit --grep to this directory or file (repeatable)")
    ap.add_argument("--context", type=int, default=0, help="lines of context around each match")
    ap.add_argument("files", nargs="*", help="file[:from-to] relative to the repository root")
    args = ap.parse_args(argv)
    if not args.repo.is_dir():
        print(f"error: {args.repo} is not a directory", file=sys.stderr)
        return 2
    if not args.grep and not args.files:
        ap.error("give files to show or --grep <regex>")
    if args.context < 0:
        ap.error("--context must be 0 or more")
    repo = Repo(args.repo)
    lines: list[str] = []
    if args.grep:
        try:
            lines += grep(repo, re.compile(args.grep), args.context, args.scope)
        except re.error as exc:
            print(f"error: bad --grep pattern: {exc}", file=sys.stderr)
            return 2
    for spec in args.files:
        lines += show(repo, spec)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
