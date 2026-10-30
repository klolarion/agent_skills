"""Select the check items that apply to a service profile.

Usage:
    python select_items.py --traits ai,agent,pii --forms web,api --stage beta --region KR
    python select_items.py --profile profile.json

Profile JSON keys: traits (list), forms (list), stage, region, agent_side_effects.
Prints JSON: target level, loaded packs, selected items (the fixed to-do list),
and excluded items with the reason (reported as N/A). Selected items that carry a
policy_question include it, so the question can be asked alongside the review.

Rules:
- Target level: prototype -> L1, beta/production -> L2,
  production + (payment or pii or irreversible agent) -> L3.
- Packs: core always, trait packs named in traits, the region pack named by region.
- Item traits: every listed trait must be in the profile. Item forms: any overlap.
  Empty traits/forms on an item mean "applies to all".
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from packs import default_packs_dir, load_all  # noqa: E402

LEVELS = {"L1": 1, "L2": 2, "L3": 3}
STAGES = ("prototype", "beta", "production")


def _names(profile: dict, key: str) -> set[str]:
    """traits / forms as a set; a string here would otherwise become a set of characters."""
    value = profile.get(key)
    if value is None:
        return set()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{key} must be a list of names, e.g. [\"ai\", \"pii\"], got {value!r}")
    return set(value)


def target_level(profile: dict) -> str:
    stage = profile.get("stage")
    if stage is None:
        raise ValueError(f"stage is required, one of {STAGES}; ask the user instead of assuming one")
    if stage not in STAGES:
        raise ValueError(f"unknown stage '{stage}', expected one of {STAGES}")
    if stage == "prototype":
        return "L1"
    traits = _names(profile, "traits")
    irreversible = "agent" in traits and profile.get("agent_side_effects") == "irreversible"
    if stage == "production" and ({"payment", "pii"} & traits or irreversible):
        return "L3"
    return "L2"


def select(profile: dict, packs_dir: Path | None = None) -> dict:
    packs = load_all(packs_dir or default_packs_dir())
    traits = _names(profile, "traits")
    forms = _names(profile, "forms")
    region = (profile.get("region") or "").lower()
    level = target_level(profile)

    loaded, selected, excluded = [], [], []
    for name, (meta, items) in packs.items():
        axis = meta.get("axis")
        if not (axis == "core" or (axis == "trait" and name in traits) or (axis == "region" and name == region)):
            continue
        entry = {"pack": name, "axis": axis, "updated": meta.get("updated")}
        verified = sorted(str(i["verified"]) for i in items if i.get("verified"))
        if verified:  # legal citations age by when they were checked, not when the pack was edited
            entry["oldest_verified"] = verified[0]
        loaded.append(entry)
        for item in items:
            row = {"id": item["id"], "pack": name, "title": item["title"], "level": item["level"], "method": item["method"]}
            missing = [t for t in item.get("traits", []) if t not in traits]
            if missing:
                excluded.append({**row, "reason": f"trait not in profile: {', '.join(missing)}"})
            elif item.get("forms") and forms and not (set(item["forms"]) & forms):
                excluded.append({**row, "reason": f"form not in profile: {', '.join(item['forms'])}"})
            elif LEVELS[item["level"]] > LEVELS[level]:
                excluded.append({**row, "reason": f"above target level {level}"})
            else:
                selected.append({**row, "blocker": bool(item.get("blocker", False)),
                                 **({"policy_question": item["policy_question"]} if "policy_question" in item else {})})

    # Blockers first, then by level, then core -> trait -> region packs, then ID.
    axis_order = {"core": 0, "trait": 1, "region": 2}
    pack_rank = {p["pack"]: (axis_order.get(p["axis"], 9), p["pack"]) for p in loaded}
    selected.sort(key=lambda r: (not r["blocker"], LEVELS[r["level"]], pack_rank[r["pack"]], r["id"]))
    return {"target_level": level, "profile": profile, "packs": loaded, "items": selected, "excluded": excluded}


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()] if value else []


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to a legacy code page
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", type=Path, help="profile JSON file")
    ap.add_argument("--traits", help="comma list, e.g. ai,agent,pii,payment")
    ap.add_argument("--forms", help="comma list, e.g. web,api,worker")
    ap.add_argument("--stage", choices=STAGES)
    ap.add_argument("--region", help="e.g. KR")
    ap.add_argument("--agent-side-effects", choices=["none", "read_only", "write", "irreversible"])
    ap.add_argument("--packs", type=Path, help="packs directory (default: ../references/packs)")
    args = ap.parse_args(argv)

    profile = {}
    if args.profile:
        try:
            profile = json.loads(args.profile.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            ap.error(f"cannot read --profile {args.profile}: {exc}")
        if not isinstance(profile, dict):
            ap.error(f"--profile {args.profile} must hold a JSON object")
    if args.traits is not None:
        profile["traits"] = _csv(args.traits)
    if args.forms is not None:
        profile["forms"] = _csv(args.forms)
    for key in ("stage", "region", "agent_side_effects"):
        if getattr(args, key) is not None:
            profile[key] = getattr(args, key)
    if profile.get("stage") is None:
        ap.error("--stage (or 'stage' in --profile) is required; ask the user, it decides the target level")

    try:
        result = select(profile, args.packs)
    except ValueError as exc:  # bad stage, traits, or forms in a --profile file
        ap.error(str(exc))
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
