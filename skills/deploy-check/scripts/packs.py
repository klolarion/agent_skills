"""Parser for check pack files (restricted YAML subset, standard library only).

Allowed grammar (anything else is an error):

    # full-line comment
    - key: value            # a new entry starts at column 0
      key: value            # continuation key, 2-space indent
      key: [a, b, c]        # inline list (items must not contain commas)
      key: true             # booleans: true / false
      key:                  # nested map, one level only
        sub: value          # 4-space indent

Values may be wrapped in single or double quotes to keep a leading "[" or a
literal "true". The first entry of a pack file is its metadata entry (has a
"pack" key); every other entry is a check item (has an "id" key).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ENTRY = re.compile(r"^- ([a-z_]+):(?: (.*))?$")
KEY = re.compile(r"^  ([a-z_]+):(?: (.*))?$")
SUBKEY = re.compile(r"^    ([a-z_]+):(?: (.*))?$")


class PackError(ValueError):
    """Raised with a file name and line number when a pack file breaks the grammar."""


def _value(raw: str):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        return [_value(part) for part in inner.split(",")] if inner else []
    if raw == "true":
        return True
    if raw == "false":
        return False
    return raw


def parse(text: str, source: str = "<string>") -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None
    nested_key: str | None = None
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line != line.rstrip():
            line = line.rstrip()
        where = f"{source}:{lineno}"
        if m := ENTRY.match(line):
            current = {}
            entries.append(current)
            nested_key = None
            key, raw = m.group(1), m.group(2)
            if raw is None:
                raise PackError(f"{where}: an entry must start with 'key: value'")
            current[key] = _value(raw)
        elif m := KEY.match(line):
            if current is None:
                raise PackError(f"{where}: key outside of an entry")
            key, raw = m.group(1), m.group(2)
            if key in current:
                raise PackError(f"{where}: duplicate key '{key}'")
            if raw is None:
                current[key] = {}
                nested_key = key
            else:
                current[key] = _value(raw)
                nested_key = None
        elif m := SUBKEY.match(line):
            if current is None or nested_key is None:
                raise PackError(f"{where}: nested key without a parent 'key:' line")
            key, raw = m.group(1), m.group(2)
            if raw is None:
                raise PackError(f"{where}: only one level of nesting is allowed")
            current[nested_key][key] = _value(raw)
        else:
            raise PackError(f"{where}: line does not match the pack grammar: {line.strip()[:60]}")
    return entries


def load_pack(path: Path) -> tuple[dict, list[dict]]:
    """Return (metadata, items) for one pack file."""
    entries = parse(Path(path).read_text(encoding="utf-8"), source=str(path))
    if not entries or "pack" not in entries[0]:
        raise PackError(f"{path}: first entry must be the pack metadata (pack: <name>)")
    meta, items = entries[0], entries[1:]
    for item in items:
        item["pack"] = meta["pack"]
    return meta, items


def load_all(packs_dir: Path) -> dict[str, tuple[dict, list[dict]]]:
    """Load every *.yaml pack in a directory, keyed by pack name."""
    packs = {}
    for path in sorted(Path(packs_dir).glob("*.yaml")):
        meta, items = load_pack(path)
        packs[meta["pack"]] = (meta, items)
    return packs


def default_packs_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "references" / "packs"


def main(argv: list[str]) -> int:
    """`packs.py --item ID[,ID...]` prints those entries; `packs.py [dir]` summarizes the packs."""
    import json

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    usage = ("usage: packs.py [packs_dir | pack.yaml]   summarize packs\n"
             "       packs.py --item ID[,ID...]          print entries as JSON\n"
             "\n"
             "Entry keys: id, pack, title, question, level, method, blocker, traits, forms,\n"
             "  pass / partial / fail   the verdict texts (partial is null: all or nothing)\n"
             "  evidence                search starting points, not proof\n"
             "  policy_question         ask it when the verdict depends on the answer\n"
             "  remediation             the fix to report\n"
             "  source, verified        legal citation and when it was checked (region packs)\n"
             "  origin                  why the item exists; not a judging rule")
    if len(argv) > 1 and argv[1] in ("-h", "--help"):
        print(usage)
        return 0
    if len(argv) > 1 and argv[1] == "--item":
        wanted = {i.strip() for i in argv[2].split(",") if i.strip()} if len(argv) > 2 else set()
        if not wanted:
            print(usage, file=sys.stderr)
            print("error: --item needs at least one ID", file=sys.stderr)
            return 2
        try:
            found = [it for _, items in load_all(default_packs_dir()).values() for it in items if it["id"] in wanted]
        except (PackError, OSError, UnicodeDecodeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        for it in found:
            it.setdefault("partial", None)  # all-or-nothing items have no partial text
        missing = wanted - {it["id"] for it in found}
        print(json.dumps(found, ensure_ascii=False, indent=2))
        if missing:
            print(f"unknown ids: {sorted(missing)}", file=sys.stderr)
            return 1
        return 0
    target = Path(argv[1]) if len(argv) > 1 else default_packs_dir()
    if not target.exists():
        print(usage, file=sys.stderr)
        print(f"error: {target} does not exist", file=sys.stderr)
        return 2
    try:
        data = load_all(target) if target.is_dir() else {"file": load_pack(target)}
    except (PackError, OSError, UnicodeDecodeError) as exc:  # a grammar error names the file and line
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({k: {"meta": m, "items": len(i)} for k, (m, i) in data.items()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
