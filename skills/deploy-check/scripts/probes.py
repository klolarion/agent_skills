"""Deterministic probes: check items that can be decided from repository files alone.

Usage:
    python probes.py --repo <path> [--ids CORE-SEC-01,CORE-DEP-01] [--format json|markdown]

Guarantees: read-only, no network, never executes repository code,
standard library only, results on stdout. Secrets are never echoed: findings report
the file, line, and pattern name only.

Each probe returns: {id, outcome, confidence, locations, message}
    outcome: pass | fail | n/a | review | error
    (n/a = precondition absent, review = evidence the script cannot decide alone; the message
    says what to check, error = could not decide)
    confidence: high | medium. A pass that rests on a limited check (a pattern list, not a full
    scan) is medium; a probe never claims more than it looked at.

Scanner reports from CI can be passed with --gate (see gates.py); deploy-check never runs scanners.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gates as gate_reports  # noqa: E402

IGNORED_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build",
                ".next", ".nuxt", "coverage", ".tox", ".mypy_cache", ".pytest_cache", "vendor", "target"}
MAX_FILE_BYTES = 1_000_000
COMMIT_RE = re.compile(r"[0-9a-fA-F]{7,64}")  # --gate-commit: a full or abbreviated (7+) commit hash
COMMENT_PREFIXES = ("#", "//", "/*", "*")


# --- file access -----------------------------------------------------------

class Repo:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.files, self.source = self._list_files()
        self.gates: list[gate_reports.Gate] = []
        self.gate_commit_confirmed = False

    def head(self) -> tuple[str | None, bool]:
        """(HEAD commit, tracked files changed) or (None, True) outside git."""
        if not ((self.root / ".git").exists() and shutil.which("git")):
            return None, True
        git = ["git", "-c", "core.fsmonitor=false", "-C", str(self.root)]
        try:
            sha = subprocess.run(git + ["rev-parse", "HEAD"], capture_output=True, check=True, timeout=30).stdout.decode().strip()
            # Comparing the work tree re-hashes racily clean files through filter.<name>.clean/process
            # from the audited repo's config; blank every configured filter so none can run.
            names = self._filter_names(git)
            if names is None:
                return sha, True
            off = [a for n in sorted(names) for v in ("clean=", "smudge=", "process=", "required=false")
                   for a in ("-c", f"filter.{n}.{v}")]
            # diff-index never writes the index; "dirty" keeps it from running git status inside submodules.
            changed = subprocess.run(git + off + ["diff-index", "--quiet", "--ignore-submodules=dirty", "HEAD", "--"],
                                     capture_output=True, timeout=30).returncode
            return sha, changed != 0
        except (subprocess.SubprocessError, OSError):
            return None, True

    @staticmethod
    def _filter_names(git: list[str]) -> set[str] | None:
        """Filter driver names from every config level, or None when one cannot be overridden with -c."""
        r = subprocess.run(git + ["config", "-z", "--get-regexp", r"^filter\."], capture_output=True, timeout=30)
        if r.returncode not in (0, 1):  # 1 = no filter configured
            return None
        names = set()
        for entry in r.stdout.decode("utf-8", "replace").split("\0"):
            key = entry.partition("\n")[0]
            if key.count(".") >= 2:
                names.add(key[len("filter."):key.rindex(".")])
        return None if any("=" in n for n in names) else names

    def gates_of(self, kind: str) -> list[gate_reports.Gate]:
        return [g for g in self.gates if g.kind == kind]

    def _list_files(self) -> tuple[list[Path], str]:
        """Prefer tracked files (git ls-files); fall back to walking the tree."""
        if (self.root / ".git").exists() and shutil.which("git"):
            try:
                # The audited repo's .git/config can name an fsmonitor program; never run it.
                out = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(self.root), "ls-files", "-z"],
                                     capture_output=True, check=True, timeout=30).stdout
                paths = [self.root / p for p in out.decode("utf-8", "replace").split("\0") if p]
                return [p for p in paths if p.is_file() and self._inside(p)], "git ls-files"
            except (subprocess.SubprocessError, OSError):
                pass
        paths = []
        for p in self.root.rglob("*"):
            if p.is_file() and not (set(p.relative_to(self.root).parts[:-1]) & IGNORED_DIRS) and self._inside(p):
                paths.append(p)
        return sorted(paths), "directory walk"

    def _inside(self, p: Path) -> bool:
        """False for a symlink (or a path through one) that leaves the repository, e.g. to ~/.netrc."""
        try:
            return self.root in p.resolve().parents
        except (OSError, RuntimeError):  # a symlink loop
            return False

    def rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    def too_large(self, p: Path) -> bool:
        """True when read() returns "" because the file is over MAX_FILE_BYTES, not because it is empty."""
        try:
            return p.stat().st_size > MAX_FILE_BYTES
        except OSError:
            return False

    def read(self, p: Path) -> str:
        """File text; "" for a file over MAX_FILE_BYTES (see too_large) or one that cannot be read."""
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                return ""
            return p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def find(self, *names: str) -> list[Path]:
        wanted = set(names)
        return [p for p in self.files if p.name in wanted]

    def glob(self, pattern: str) -> list[Path]:
        return [p for p in self.files if p.match(pattern)]

    def grep(self, regex: re.Pattern, paths: list[Path] | None = None, skip_comments: bool = False) -> list[str]:
        hits = []
        for p in paths if paths is not None else self.files:
            for n, line in enumerate(self.read(p).splitlines(), 1):
                if skip_comments and line.lstrip().startswith(COMMENT_PREFIXES):
                    continue
                if regex.search(line):
                    hits.append(f"{self.rel(p)}:{n}")
        return hits


def result(item_id: str, outcome: str, message: str, locations: list[str] | None = None, confidence: str = "high") -> dict:
    return {"id": item_id, "outcome": outcome, "confidence": confidence, "locations": (locations or [])[:20], "message": message}


# Jenkins and CodeBuild pipelines are defined in the repo, but what triggers them is configured outside it.
EXTERNAL_TRIGGER_CI = re.compile(r"^(Jenkinsfile(\..+)?|buildspec.*\.ya?ml)$")


def ci_files(repo: Repo) -> list[Path]:
    return [p for p in repo.files if (".github/workflows/" in repo.rel(p) and p.suffix in (".yml", ".yaml"))
            or repo.rel(p) in (".gitlab-ci.yml", ".circleci/config.yml", "azure-pipelines.yml", "bitbucket-pipelines.yml")
            or EXTERNAL_TRIGGER_CI.match(p.name)]


# --- probes ----------------------------------------------------------------

# A fallback for repositories without a secret scanner, not a replacement for one: keep it to common,
# unambiguous formats. Broad coverage comes from a gitleaks report passed with --gate.
SECRET_PATTERNS = {
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----"),
    "anthropic-key": re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
    # Project/service keys contain "_" and "-"; legacy keys are 48 alphanumerics.
    "openai-key": re.compile(r"\bsk-(?:(?:proj|svcacct|admin)-[A-Za-z0-9_\-]{20,}|[A-Za-z0-9]{32,})"),
    "stripe-key": re.compile(r"\b[sr]k_live_[A-Za-z0-9]{20,}"),
    "toss-key": re.compile(r"\blive_g?sk_[A-Za-z0-9]{16,}"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "slack-token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    # Matches prefixed names too (DB_PASSWORD, OPENAI_API_KEY) assigned a quoted literal.
    "generic-assignment": re.compile(
        r"(?i)[\w.-]*(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)[\w.-]*['\"]?\s*[:=]\s*['\"]([^'\"\s]{8,})['\"]"),
}
PLACEHOLDER = re.compile(r"(?i)(changeme|change-me|example|placeholder|your[_-]|xxx+|dummy|<[^>]+>|\$\{|\{\{|redacted|\*\*\*)")
# Lowercase words joined by "_" or "." (password_hash, user.password) name a field, not a secret.
IDENTIFIER = re.compile(r"^[a-z]+(?:[_.][a-z]+)+$")
SKIP_SUFFIXES = (".example", ".sample", ".template", ".lock", ".min.js", ".map", ".svg", ".png", ".jpg", ".pdf")
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "fixtures", "fixture", "testdata", "__mocks__", "mocks"}
TEST_FILE = re.compile(r"(^test_.*\.py$|_test\.(py|go)$|\.(test|spec)\.[cm]?[jt]sx?$|Tests?\.(java|kt)$)")


# A value that names where a secret lives (secret manager ARN, vault path) is a reference, not the secret.
SECRET_REFERENCE = re.compile(r"^(arn:aws:(secretsmanager|ssm):|(vault|ssm|secretsmanager|op|gcp-secret|azure-keyvault)://)")
# CodeBuild maps env names to "secret-id:json-key" references inside these blocks.
REFERENCE_BLOCK = re.compile(r"^(\s*)(secrets-manager|parameter-store)\s*:\s*$")
# Bundlers inline these into client code, so they hold public values by design.
PUBLIC_ENV_PREFIXES = ("VITE_", "NEXT_PUBLIC_", "REACT_APP_", "EXPO_PUBLIC_", "NUXT_PUBLIC_", "PUBLIC_", "GATSBY_", "VUE_APP_")
ENV_KEY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def is_test_path(repo: Repo, p: Path) -> bool:
    parts = p.relative_to(repo.root).parts
    return bool(set(parts[:-1]) & TEST_DIRS) or bool(TEST_FILE.search(parts[-1]))


def _only_public_env_keys(text: str) -> bool:
    keys = [m.group(1) for m in map(ENV_KEY.match, text.splitlines()) if m]
    return all(k.startswith(PUBLIC_ENV_PREFIXES) for k in keys)


def probe_committed_secrets(repo: Repo) -> dict:
    found, in_tests = [], []
    for p in repo.files:
        name = p.name
        bucket = in_tests if is_test_path(repo, p) else found
        if name == ".env" or (name.startswith(".env.") and not name.endswith(SKIP_SUFFIXES)):
            if not _only_public_env_keys(repo.read(p)):
                bucket.append(f"{repo.rel(p)} (tracked env file)")
                continue
            # Only public bundler keys: not a secrets file, but still scan its values below.
        if name.endswith(SKIP_SUFFIXES):
            continue
        block_indent = None
        for n, line in enumerate(repo.read(p).splitlines(), 1):
            indent = len(line) - len(line.lstrip())
            if block_indent is not None and line.strip() and indent <= block_indent:
                block_indent = None
            if b := REFERENCE_BLOCK.match(line):
                block_indent = len(b.group(1))
                continue
            for kind, rx in SECRET_PATTERNS.items():
                m = rx.search(line)
                if not m:
                    continue
                value = m.group(1) if m.groups() else m.group(0)
                if kind == "generic-assignment" and (PLACEHOLDER.search(value) or IDENTIFIER.match(value)
                                                     or SECRET_REFERENCE.match(value) or block_indent is not None):
                    continue
                bucket.append(f"{repo.rel(p)}:{n} ({kind})")
                break
    tagged = [f"{loc} [test path]" for loc in in_tests]
    scanners = repo.gates_of("secrets")
    # A scanner finding counts as committed: it is in the tree or in git history, which is published too.
    from_scanners = [f for g in scanners for f in g.findings]
    found += from_scanners
    if found:
        where = "tracked files or history" if from_scanners else "tracked files"
        extra = f" (+{len(in_tests)} in test or fixture paths)" if in_tests else ""
        return result("CORE-SEC-01", "fail", f"{len(found)} possible secret(s) in {where}{extra}; values not shown", found + tagged)
    if in_tests:
        # Test credentials are often fake, but a real key in a fixture still leaks. The agent decides.
        return result("CORE-SEC-01", "review",
                      f"{len(in_tests)} possible secret(s) only in test or fixture paths; judge each location", tagged)
    if scanners and repo.gate_commit_confirmed:
        names = ", ".join(sorted({g.tool for g in scanners}))
        return result("CORE-SEC-01", "pass", f"{names} report for the audited commit has no findings; known patterns also absent "
                      f"in {len(repo.files)} tracked files", [g.source for g in scanners])
    # Without a scanner report this is a pattern check, not a full secret scan: never more than medium.
    return result("CORE-SEC-01", "pass", f"no known credential patterns in {len(repo.files)} tracked files ({repo.source}); "
                  "pattern check only, not a full secret scan", confidence="medium")


ENV_IGNORE = re.compile(r"^\s*/?(?:\*\*/)?(?:\.env(?:\*|\.\*)?|\*\.env|\.env\.local)\s*$")


def probe_env_ignored(repo: Repo) -> dict:
    ignores = repo.find(".gitignore")
    root_ignore = repo.root / ".gitignore"
    if root_ignore.is_file() and root_ignore not in ignores:
        ignores.append(root_ignore)
    if not ignores:
        return result("CORE-SEC-02", "fail", "no .gitignore found")
    hits = repo.grep(ENV_IGNORE, ignores)
    if hits:
        return result("CORE-SEC-02", "pass", ".env is ignored", hits)
    return result("CORE-SEC-02", "fail", ".gitignore has no pattern matching .env", [repo.rel(p) for p in ignores])


SCANNERS = re.compile(r"(?i)\b(gitleaks|trufflehog|detect-secrets|ggshield|secretlint)\b")


def probe_secret_scanning(repo: Repo) -> dict:
    hits = repo.grep(SCANNERS, repo.find(".pre-commit-config.yaml") + ci_files(repo), skip_comments=True)
    if hits:
        return result("CORE-SEC-03", "pass", "secret scanner configured", hits)
    return result("CORE-SEC-03", "fail", "no secret scanner in pre-commit or CI")


LOCKS = {
    "package.json": ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock", "npm-shrinkwrap.json"),
    "pyproject.toml": ("uv.lock", "poetry.lock", "pdm.lock", "Pipfile.lock", "requirements.txt"),
    "Pipfile": ("Pipfile.lock",),
    "go.mod": ("go.sum",),
    "Cargo.toml": ("Cargo.lock",),
    "Gemfile": ("Gemfile.lock",),
    "composer.json": ("composer.lock",),
}
# Workspaces (npm/pnpm/yarn/bun, Cargo, uv) keep one lock file at the workspace root. The root lock
# covers a nested manifest only when that root declares a workspace: (declaration file, marker regex).
WORKSPACE_LOCKS = {
    "package.json": LOCKS["package.json"],
    "Cargo.toml": ("Cargo.lock",),
    "pyproject.toml": ("uv.lock",),
}
WORKSPACE_DECLARATIONS = {
    "package.json": (("package.json", re.compile(r'"workspaces"\s*:')), ("pnpm-workspace.yaml", re.compile(r"^packages\s*:", re.M))),
    "Cargo.toml": (("Cargo.toml", re.compile(r"^\[workspace\]", re.M)),),
    "pyproject.toml": (("pyproject.toml", re.compile(r"^\[tool\.uv\.workspace\]", re.M)),),
}
PINNED = re.compile(r"^\s*[A-Za-z0-9_.\-\[\]]+\s*==\s*[^\s;#]+")


def _requirements_pinned(repo: Repo, p: Path) -> bool:
    lines = [ln for ln in repo.read(p).splitlines() if ln.strip() and not ln.lstrip().startswith(("#", "-"))]
    return bool(lines) and all(PINNED.match(ln) for ln in lines)


# JVM builds pin by declaring exact versions; dynamic versions and ranges float like an unlocked manifest.
JVM_MANIFESTS = ("build.gradle", "build.gradle.kts", "pom.xml")
JVM_DYNAMIC_VERSION = re.compile(r"""([:'"][\d.]*\+['"]|latest\.(release|integration)|<version>\s*[\[(]|<version>\s*(LATEST|RELEASE)\s*<)""")


def probe_lock_files(repo: Repo) -> dict:
    names_by_dir: dict[Path, dict[str, Path]] = {}
    for p in repo.files:
        names_by_dir.setdefault(p.parent, {})[p.name] = p
    shallow = [p for p in repo.files if len(p.relative_to(repo.root).parts) <= 4]
    manifests = [p for p in shallow if p.name in LOCKS]
    # A requirements.txt is its own manifest unless a pyproject.toml or Pipfile beside it owns the dependencies.
    reqs = [p for p in shallow if p.name == "requirements.txt"
            and not {"pyproject.toml", "Pipfile"} & set(names_by_dir.get(p.parent, {}))]
    jvm = [p for p in shallow if p.name in JVM_MANIFESTS]
    if not (manifests or reqs or jvm):
        return result("CORE-DEP-01", "n/a", "no package manifest found")
    missing, ok = [], []
    for r in reqs:
        if _requirements_pinned(repo, r):
            ok.append(repo.rel(r))
        else:
            missing.append(f"{repo.rel(r)} (not fully pinned)")
    for j in jvm:
        locked = "gradle.lockfile" in names_by_dir.get(j.parent, {})
        floating = repo.grep(JVM_DYNAMIC_VERSION, [j])
        if locked or not floating:
            ok.append(repo.rel(j))
        else:
            missing.append(f"{floating[0]} (dynamic version)")
    for m in manifests:
        siblings = names_by_dir.get(m.parent, {})
        locks = [siblings[n] for n in LOCKS[m.name] if n in siblings]
        if m.name == "pyproject.toml":
            locks = [p for p in locks if p.name != "requirements.txt" or _requirements_pinned(repo, p)]
        if not locks and m.name in WORKSPACE_LOCKS:
            for parent in m.parent.parents:
                if parent != repo.root and repo.root not in parent.parents:
                    break
                found = names_by_dir.get(parent, {})
                declared = any(f in found and rx.search(repo.read(found[f])) for f, rx in WORKSPACE_DECLARATIONS[m.name])
                locks = [found[n] for n in WORKSPACE_LOCKS[m.name] if n in found] if declared else []
                if locks:
                    break
        (ok if locks else missing).append(repo.rel(m))
    if missing:
        return result("CORE-DEP-01", "fail", "manifest without a lock file or pinned versions", missing)
    return result("CORE-DEP-01", "pass", "every manifest has a lock file or pinned versions", ok)


AUDIT_TOOLS = re.compile(r"(?i)\b(pip-audit|npm audit|pnpm audit|yarn audit|osv-scanner|snyk|trivy|safety check|cargo audit|govulncheck)\b")


def probe_dependency_scanning(repo: Repo) -> dict:
    configs = [repo.rel(p) for p in repo.files if repo.rel(p) in (
        ".github/dependabot.yml", ".github/dependabot.yaml", "renovate.json", "renovate.json5",
        ".renovaterc", ".renovaterc.json", ".github/renovate.json")]
    hits = configs + repo.grep(AUDIT_TOOLS, ci_files(repo), skip_comments=True)
    if hits:
        return result("CORE-DEP-02", "pass", "dependency scanning configured", hits)
    return result("CORE-DEP-02", "fail", "no Dependabot/Renovate config and no audit step in CI")


ECOSYSTEMS = {"package.json": "npm", "pyproject.toml": "pypi", "requirements.txt": "pypi", "Pipfile": "pypi",
              "go.mod": "go", "Cargo.toml": "cargo", "Gemfile": "rubygems", "composer.json": "packagist",
              "pom.xml": "maven", "build.gradle": "maven", "build.gradle.kts": "maven"}


def probe_known_vulnerabilities(repo: Repo) -> dict:
    present = {ECOSYSTEMS[p.name] for p in repo.files if p.name in ECOSYSTEMS}
    if not present:
        return result("CORE-DEP-03", "n/a", "no package manifest found")
    reports = repo.gates_of("dependencies")
    if not reports:
        return result("CORE-DEP-03", "error", "no dependency scanner report given (--gate npm-audit=... / pip-audit=... / sarif=...); "
                      "deploy-check does not run scanners, so known vulnerabilities are unchecked")
    findings = [f for g in reports for f in g.findings]
    if findings:
        return result("CORE-DEP-03", "fail", f"{len(findings)} high or critical vulnerability finding(s) in scanner reports", findings)
    covered = set().union(*(g.covers for g in reports))
    missing = sorted(present - covered) if "*" not in covered else []
    sources = [g.source for g in reports]
    if missing:
        return result("CORE-DEP-03", "review", f"reports are clean but do not cover: {', '.join(missing)}", sources)
    if not repo.gate_commit_confirmed:
        return result("CORE-DEP-03", "review", "reports are clean but are not confirmed to be for the audited commit "
                      "(pass --gate-commit, and audit a clean working tree)", sources)
    return result("CORE-DEP-03", "pass", "scanner reports for the audited commit list no high or critical vulnerability", sources)


TEST_CMD = re.compile(r"(?i)(?<![\w-])(pytest|python -m unittest|npm (run )?test|pnpm (run )?test|yarn test|bun test|vitest|jest"
                      r"|go test|cargo test|(mvnw?|gradlew?)(\s+[-\w:.=]+)*?\s+(test|verify|check)|rspec|phpunit|tox|make test)(?![\w.-])")
# Installing a test runner is not running it: "pip install pytest" must not count.
INSTALL_CMD = re.compile(r"(?i)\b(pip3? install|uv (pip install|add|sync)|poetry (add|install)|npm (install|ci|i)\b|pnpm (install|add|i)\b"
                         r"|yarn (add|install)|bun (add|install)|apt(-get)? install|brew install|gem install|go install|cargo install)")
CMD_SEPARATOR = re.compile(r"&&|\|\||;")
# "pytest || true" runs the suite but can never fail the build.
FAILURE_SWALLOWED = re.compile(r"\|\|\s*(true|:|exit 0)\b")
# Step names and other keys describe a step; only commands run it ("name: Run pytest" is not a test run).
CI_META_LINE = re.compile(r"^\s*-?\s*(name|id|if|uses|with|description|label|stage)\s*:")
CONTINUE_ON_ERROR = re.compile(r"(?im)^\s*(continue-on-error|allow_failure)\s*:\s*true\b")
# A Jenkinsfile can declare its own push trigger; a cron schedule does not run on every change.
JENKINS_PUSH_TRIGGER = re.compile(r"\b(pollSCM|githubPush|gitlab|bitbucketPush)\s*\(")
# A pipeline that ships the production release: pushes an image, rolls out, or targets the production environment.
RELEASE_STEP = re.compile(r"(?i)(^\s*environment\s*:\s*['\"]?prod(uction)?\b|\bdocker\s+push\b|\bkubectl\s+(apply|set\s+image|rollout\s+restart)\b"
                          r"|\bhelm\s+(upgrade|install)\b|\baws\s+ecs\s+update-service\b|\bgcloud\s+run\s+deploy\b|\bflyctl\s+deploy\b"
                          r"|\bdeploy[\w.-]*\.sh\b(?!\s+(staging|stage|dev|test)\b))")
# needs: (same workflow) or workflow_run: (another workflow) makes a release job wait for the tests.
WAITS_FOR_RUN = re.compile(r"(?m)^\s*(needs|workflow_run)\s*:")


def release_without_tests(repo: Repo, files: list[Path], tested: set[str]) -> list[str]:
    """First release line of each pipeline that ships the release but neither runs tests nor waits for them."""
    gaps = []
    for p in files:
        rel = repo.rel(p)
        text = repo.read(p)
        if rel in tested or WAITS_FOR_RUN.search(text):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if not line.lstrip().startswith("#") and RELEASE_STEP.search(line):
                gaps.append(f"{rel}:{n}")
                break
    return gaps


def probe_ci_tests(repo: Repo) -> dict:
    files = ci_files(repo)
    if not files:
        return result("CORE-CI-01", "fail", "no CI workflow found")
    hits, swallowed, may_ignore = [], [], []
    for p in files:
        text = repo.read(p)
        ignores_failure = bool(CONTINUE_ON_ERROR.search(text))
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#") or CI_META_LINE.match(line):
                continue
            if any(TEST_CMD.search(seg) and not INSTALL_CMD.search(seg) for seg in CMD_SEPARATOR.split(line)):
                loc = f"{repo.rel(p)}:{n}"
                (swallowed if FAILURE_SWALLOWED.search(line) else may_ignore if ignores_failure else hits).append(loc)
    on_push = [h for h in hits if not EXTERNAL_TRIGGER_CI.match(Path(h.rsplit(":", 1)[0]).name)
               or JENKINS_PUSH_TRIGGER.search(repo.read(repo.root / h.rsplit(":", 1)[0]))]
    untested = release_without_tests(repo, files, {h.rsplit(":", 1)[0] for h in hits + may_ignore})
    if (on_push or hits) and untested:
        return result("CORE-CI-01", "review", "CI runs tests, but a pipeline that ships the production release neither "
                      "runs nor waits for them; judge by judging.md \"Several pipelines\"", hits + untested)
    if on_push:
        return result("CORE-CI-01", "pass", "CI runs tests", on_push)
    if may_ignore:
        return result("CORE-CI-01", "review", "the test step is in a file that sets continue-on-error or allow_failure; "
                      "check whether it applies to the test step", may_ignore)
    if hits:
        return result("CORE-CI-01", "review", "tests run only in Jenkins or CodeBuild, whose trigger is configured outside "
                      "the repository; confirm they run on every push or pull request", hits)
    if swallowed:
        return result("CORE-CI-01", "fail", "CI runs tests but ignores their failure", swallowed)
    return result("CORE-CI-01", "fail", "CI exists but no test command found", [repo.rel(p) for p in files])


# Only an init/configure call counts: a declared dependency or an import does nothing until wired in.
ERROR_TRACKER_INIT = re.compile(
    r"(\b(sentry_sdk|Sentry|sentry)\.(init|Init)\b|\b[Rr]ollbar\.init\b|\bnew Rollbar\(|\b[Bb]ugsnag\.(start|configure)\b"
    r"|\b[Hh]oneybadger\.configure\b|\bdatadogLogs\.init\b)")
ERROR_TRACKER_DEP = re.compile(r"(?i)(@sentry/|sentry[-_]sdk|getsentry/sentry-go|rollbar|bugsnag|honeybadger|@datadog/browser-logs)")
CODE_SUFFIXES = (".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".go", ".rb", ".java", ".kt")
NON_APP_DIRS = {"scripts", "script", "examples", "example", "samples", "sample", "docs", "doc"}
DEP_MANIFESTS = ("package.json", "pyproject.toml", "requirements.txt", "go.mod", "Gemfile", "build.gradle", "build.gradle.kts", "pom.xml")


def probe_error_tracking(repo: Repo) -> dict:
    code = [p for p in repo.files if p.suffix in CODE_SUFFIXES and not is_test_path(repo, p)]
    hits = repo.grep(ERROR_TRACKER_INIT, code, skip_comments=True)
    in_app = [h for h in hits if not set(Path(h.rsplit(":", 1)[0]).parts[:-1]) & NON_APP_DIRS]
    if in_app:
        return result("CORE-OBS-01", "pass", "error tracker initialized", in_app)
    if hits:
        return result("CORE-OBS-01", "review", "error tracker is initialized only in scripts, examples, or docs; "
                      "check that production entry points run it", hits)
    declared = repo.grep(ERROR_TRACKER_DEP, repo.find(*DEP_MANIFESTS))
    if declared:
        return result("CORE-OBS-01", "fail", "error tracking SDK is declared but never initialized", declared)
    return result("CORE-OBS-01", "fail", "no error tracking SDK found")


PROBES = {
    "CORE-SEC-01": probe_committed_secrets,
    "CORE-SEC-02": probe_env_ignored,
    "CORE-SEC-03": probe_secret_scanning,
    "CORE-DEP-01": probe_lock_files,
    "CORE-DEP-02": probe_dependency_scanning,
    "CORE-DEP-03": probe_known_vulnerabilities,
    "CORE-CI-01": probe_ci_tests,
    "CORE-OBS-01": probe_error_tracking,
}


def run(repo_path: Path, ids: list[str] | None = None, gate_specs: list[str] | None = None,
        gate_commit: str | None = None) -> list[dict]:
    """Raises gates.GateError for an unusable report; probes themselves never raise."""
    repo = Repo(repo_path)
    repo.gates = [gate_reports.load(spec, repo.root) for spec in gate_specs or []]
    if repo.gates and gate_commit:
        head, dirty = repo.head()
        repo.gate_commit_confirmed = (bool(head) and not dirty and bool(COMMIT_RE.fullmatch(gate_commit))
                                      and head.startswith(gate_commit.lower()))
    out = []
    for item_id in ids or list(PROBES):
        fn = PROBES.get(item_id)
        if fn is None:
            out.append(result(item_id, "error", "no probe implemented for this item; judge it by review"))
            continue
        try:
            out.append(fn(repo))
        except Exception as exc:  # a probe must never crash the run
            out.append(result(item_id, "error", f"probe failed: {type(exc).__name__}: {exc}"))
    return out


def to_markdown(results: list[dict]) -> str:
    lines = ["| ID | Outcome | Message | Locations |", "| --- | --- | --- | --- |"]
    for r in results:
        locs = "<br>".join(r["locations"][:5]) + (" …" if len(r["locations"]) > 5 else "")
        outcome = f"{r['outcome']} ({r['confidence']})" if r["outcome"] == "pass" else r["outcome"]
        lines.append(f"| {r['id']} | {outcome} | {r['message']} | {locs} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to a legacy code page
    ap = argparse.ArgumentParser(description="Deterministic deploy-check probes (read-only).")
    ap.add_argument("--repo", type=Path, required=True, help="path of the repository to check")
    ap.add_argument("--ids", help="comma list of item IDs (default: all probes)")
    ap.add_argument("--format", choices=["json", "markdown"], default="json")
    ap.add_argument("--gate", action="append", default=[], metavar="KIND=PATH",
                    help=f"scanner report produced outside the repository; KIND is one of {', '.join(gate_reports.KINDS)}")
    ap.add_argument("--gate-commit", help="commit the --gate reports were produced from; a clean report counts only for it")
    args = ap.parse_args(argv)
    if args.gate_commit is not None and not COMMIT_RE.fullmatch(args.gate_commit):
        ap.error(f"--gate-commit expects a commit hash of at least 7 hex characters, got {args.gate_commit!r}")
    if not args.repo.is_dir():
        print(f"error: {args.repo} is not a directory", file=sys.stderr)
        return 2
    ids = [i.strip() for i in args.ids.split(",") if i.strip()] if args.ids else None
    try:
        results = run(args.repo, ids, args.gate, args.gate_commit)
    except gate_reports.GateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        json.dump(results, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(to_markdown(results) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
