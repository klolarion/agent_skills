"""Measure how much code an audit has to read, to choose between one session and parallel workers.

Usage:
    python repo_stats.py --repo <path>

Read-only and static like probes.py: it lists tracked files (git ls-files) and counts lines.
Nothing in the repository is executed.

Lines are split into:
    audit     backend source, infrastructure, CI, and config: what most items are judged on
    frontend  client UI code; only a few items look at it (rendering, notices, privacy page)
    skipped   tests, docs, lock files, vendored or generated code, data files

mode is "single" when the audit lines fit one session comfortably, otherwise "workers".
The threshold is provisional: it rests on a few measured runs, not a benchmark.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probes import Repo  # noqa: E402

WORKER_THRESHOLD_LINES = 10_000
CHARS_PER_TOKEN = 4

SOURCE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".go", ".java", ".kt", ".kts", ".rb", ".php", ".cs", ".rs",
              ".scala", ".swift", ".ex", ".exs", ".sql", ".sh", ".tf", ".hcl", ".gradle", ".groovy"}
CONFIG_EXT = {".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".properties", ".xml", ".json", ".env"}
CONFIG_NAMES = {"Dockerfile", "Jenkinsfile", "Makefile", "Procfile", "Caddyfile", ".gitignore", ".dockerignore"}
FRONTEND_EXT = {".tsx", ".jsx", ".vue", ".svelte", ".html", ".css", ".scss", ".sass", ".less"}
FRONTEND_DIRS = {"web", "frontend", "client", "ui", "webapp", "www"}
SKIPPED_DIRS = {"node_modules", "vendor", "third_party", "dist", "build", "target", "generated", "__generated__",
                "test", "tests", "__tests__", "spec", "specs", "e2e", "testdata", "fixtures", "docs", "examples"}
LOCK_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "Pipfile.lock",
              "Cargo.lock", "go.sum", "composer.lock", "Gemfile.lock", "gradle.lockfile", "bun.lockb"}
TEST_NAME = re.compile(r"(^test_.*\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$|Tests?\.(java|kt|cs)$)")
GENERATED_NAME = re.compile(r"(\.min\.(js|css)$|_pb2(_grpc)?\.py$|\.pb\.go$|\.generated\.)")
MANIFESTS = {"package.json", "pyproject.toml", "requirements.txt", "go.mod", "pom.xml", "build.gradle",
             "build.gradle.kts", "Cargo.toml", "Gemfile", "composer.json", "Dockerfile"}
MAX_CONFIG_LINES = 1_000  # a larger config or SQL file is data (a dump, a seed), not code to audit


def classify(rel: str, lines: int) -> str:
    path = Path(rel)
    parts = set(path.parts[:-1])
    name, ext = path.name, path.suffix.lower()
    if name in LOCK_NAMES or parts & SKIPPED_DIRS or TEST_NAME.search(name) or GENERATED_NAME.search(name):
        return "skipped"
    if ext in FRONTEND_EXT or (ext in {".js", ".ts", ".mjs"} and parts & FRONTEND_DIRS):
        return "frontend"
    if ext == ".sql":
        return "audit" if lines <= MAX_CONFIG_LINES else "skipped"
    if ext in SOURCE_EXT:
        return "audit"
    if ext in CONFIG_EXT or name in CONFIG_NAMES or name.startswith(("Dockerfile", ".env")):
        return "audit" if lines <= MAX_CONFIG_LINES else "skipped"
    return "skipped"


def count_lines(p: Path) -> tuple[int, int]:
    """(lines, bytes) read in chunks, for files too large for Repo.read; same rule as for read text."""
    lines = size = 0
    last = b""
    try:
        with p.open("rb") as f:
            while chunk := f.read(1 << 20):
                lines += chunk.count(b"\n")
                size += len(chunk)
                last = chunk[-1:]
    except OSError:
        return 0, 0
    return lines + (1 if size and last != b"\n" else 0), size


def stats(repo_path: Path) -> dict:
    repo = Repo(repo_path)
    lines = {"audit": 0, "frontend": 0, "skipped": 0}
    chars = {"audit": 0, "frontend": 0}
    files = {"audit": 0, "frontend": 0, "skipped": 0}
    services = set()
    for p in repo.files:
        rel = repo.rel(p)
        if repo.too_large(p):
            n, size = count_lines(p)  # bytes stand in for characters
        else:
            text = repo.read(p)
            n, size = text.count("\n") + (1 if text and not text.endswith("\n") else 0), len(text)
        kind = classify(rel, n)
        lines[kind] += n
        files[kind] += 1
        if kind in chars:
            chars[kind] += size
        if p.name in MANIFESTS and kind != "skipped":
            services.add(Path(rel).parent.as_posix())
    mode = "single" if lines["audit"] <= WORKER_THRESHOLD_LINES else "workers"
    return {
        "source": repo.source,
        "files": files,
        "lines": lines,
        "estimated_tokens": {k: v // CHARS_PER_TOKEN for k, v in chars.items()},
        "service_dirs": sorted(services),
        "mode": mode,
        "threshold_lines": WORKER_THRESHOLD_LINES,
        "note": "mode compares audit lines with a provisional threshold; frontend lines are reported separately",
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Measure audit size (read-only).")
    ap.add_argument("--repo", type=Path, required=True, help="path of the repository to measure")
    args = ap.parse_args(argv)
    if not args.repo.is_dir():
        print(f"error: {args.repo} is not a directory", file=sys.stderr)
        return 2
    json.dump(stats(args.repo), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
