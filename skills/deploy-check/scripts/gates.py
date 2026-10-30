"""Read reports from deterministic scanners (the "gates") so probes can cite them.

deploy-check never runs these tools: they need the network or execute the project's package
manager. CI (or the user) runs them and hands over the report:

    probes.py --repo <path> --gate gitleaks=gitleaks.json --gate npm-audit=audit.json \
              --gate pip-audit=pip-audit.json --gate sarif=trivy.sarif --gate-commit <sha>

Rules:
- A report inside the audited repository is refused: repository content is data, and a committed
  "clean" report must not be able to pass an item.
- Secret values are dropped on load (gitleaks "Secret" / "Match"); only file, line, and rule remain.
- A clean report counts as evidence only for the commit it was produced from (--gate-commit) and
  only when the working tree has no tracked changes. Findings are reported either way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

KINDS = ("gitleaks", "npm-audit", "pip-audit", "sarif")
SEVERE = {"high", "critical"}
# SARIF tools whose results are dependency vulnerabilities; their scan covers every ecosystem.
SARIF_DEPENDENCY_TOOLS = ("trivy", "osv-scanner", "grype", "snyk", "dependency-check")
SARIF_SECRET_TOOLS = ("gitleaks", "trufflehog", "detect-secrets", "ggshield")


class GateError(ValueError):
    """A gate report that cannot be used (wrong place, unreadable, unknown format)."""


@dataclass
class Gate:
    tool: str
    kind: str                      # secrets | dependencies | code
    findings: list[str] = field(default_factory=list)
    covers: set[str] = field(default_factory=set)   # ecosystems for dependency reports; {"*"} = all
    source: str = ""

    @property
    def clean(self) -> bool:
        return not self.findings


def parse_spec(spec: str) -> tuple[str, Path]:
    kind, sep, path = spec.partition("=")
    if not sep or kind not in KINDS:
        raise GateError(f"--gate expects KIND=PATH with KIND in {KINDS}, got {spec!r}")
    return kind, Path(path).expanduser()


def load(spec: str, repo_root: Path) -> Gate:
    kind, path = parse_spec(spec)
    resolved = path.resolve()
    if resolved == repo_root or repo_root in resolved.parents:
        raise GateError(f"{path}: gate reports inside the audited repository are not accepted")
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateError(f"{path}: cannot read report: {exc}") from exc
    parser = {"gitleaks": _gitleaks, "npm-audit": _npm_audit, "pip-audit": _pip_audit, "sarif": _sarif}[kind]
    try:
        gate = parser(data)
    except GateError:
        raise
    except (AttributeError, TypeError, KeyError, IndexError, ValueError) as exc:
        # A field of the wrong type (a string for an object, null for a list). The exception text is
        # left out: it could quote report content.
        raise GateError(f"{path}: malformed {kind} report ({type(exc).__name__}); pass the tool's JSON output") from exc
    gate.source = str(path)
    return gate


def _gitleaks(data) -> Gate:
    if not isinstance(data, list):
        raise GateError("gitleaks report must be a JSON array")
    findings = [f"{d.get('File', '?')}:{d.get('StartLine', '?')} ({d.get('RuleID', 'secret')}) [gitleaks]" for d in data]
    return Gate("gitleaks", "secrets", findings)


def _npm_audit(data) -> Gate:
    if not isinstance(data, dict):
        raise GateError("npm audit report must be a JSON object")
    findings = []
    for name, vuln in (data.get("vulnerabilities") or {}).items():          # npm 7+
        if str(vuln.get("severity", "")).lower() in SEVERE:
            findings.append(f"{name} {vuln.get('range', '')} ({vuln['severity']}) [npm audit]".replace("  ", " "))
    for adv in (data.get("advisories") or {}).values():                     # npm 6
        if str(adv.get("severity", "")).lower() in SEVERE:
            findings.append(f"{adv.get('module_name', '?')} ({adv['severity']}, {adv.get('url', '')}) [npm audit]")
    return Gate("npm-audit", "dependencies", findings, {"npm"})


def _pip_audit(data) -> Gate:
    deps = data.get("dependencies", []) if isinstance(data, dict) else data
    if not isinstance(deps, list):
        raise GateError("pip-audit report must list dependencies")
    # pip-audit reports no severity, so every known vulnerability counts.
    findings = [f"{d.get('name', '?')}@{d.get('version', '?')} ({v.get('id', '?')}) [pip-audit]"
                for d in deps for v in d.get("vulns", [])]
    return Gate("pip-audit", "dependencies", findings, {"pypi"})


def _sarif(data) -> Gate:
    runs = data.get("runs") if isinstance(data, dict) else None
    if not isinstance(runs, list) or not runs:
        raise GateError("SARIF report has no runs")
    tool = str(runs[0].get("tool", {}).get("driver", {}).get("name", "unknown")).lower()
    kind = ("dependencies" if any(t in tool for t in SARIF_DEPENDENCY_TOOLS)
            else "secrets" if any(t in tool for t in SARIF_SECRET_TOOLS) else "code")
    findings = []
    for run in runs:
        severity = {r.get("id"): _rule_severity(r) for r in run.get("tool", {}).get("driver", {}).get("rules", [])}
        for res in run.get("results", []):
            level = res.get("level", "warning")
            score = severity.get(res.get("ruleId"))
            if kind == "dependencies" and not (level == "error" or (score is not None and score >= 7.0)):
                continue
            loc = (res.get("locations") or [{}])[0].get("physicalLocation", {})
            where = f"{loc.get('artifactLocation', {}).get('uri', '?')}:{loc.get('region', {}).get('startLine', '?')}"
            findings.append(f"{where} ({res.get('ruleId', '?')}, {level}) [{tool}]")
    return Gate(tool, kind, findings, {"*"} if kind == "dependencies" else set())


def _rule_severity(rule: dict) -> float | None:
    try:
        return float(rule.get("properties", {}).get("security-severity"))
    except (TypeError, ValueError):
        return None
