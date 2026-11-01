#!/usr/bin/env python3
"""Check the structured spec (.claude/docs/requirements/spec.json) and the IDs the work cites.

Errors (exit 1):
  - an ID (BR, PR, FR, TR or an acceptance row) appears twice;
  - a parent is missing, of the wrong level (PR to BR, FR to PR, TR to FR), or removed while the item is current;
  - an acceptance row ID does not start with its FR's ID;
  - an FR-n, FR-n.m or TR-n cited in src/test, the branch's commit messages (git log <base>..HEAD), the designs or
    the plans does not exist or is removed.
Report lines (no error): current Must FRs with no test, and FRs a test cites that spec.json lists no test for.

Python standard library only. Run from anywhere:
  python3 -I scripts/spec-check.py              # check this repository
  python3 -I scripts/spec-check.py --base main  # the branch base for commit messages (default main)
  python3 -I scripts/spec-check.py --self-test  # prove the checks on built-in cases
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = Path(".claude/docs/requirements/spec.json")
LEVELS = (("br", "BR", None), ("pr", "PR", "BR"), ("fr", "FR", "PR"), ("tr", "TR", "FR"))
CITE = re.compile(r"\b(FR|TR)-(\d+)(?:\.(\d+))?\b")
METHOD = re.compile(r"\bfr(\d+)(?:_(\d+))?_[A-Za-z]")
STATUSES = {"current", "removed"}


def index(spec: dict) -> tuple[dict[str, dict], list[str]]:
    """All items by ID, plus acceptance rows mapped to their FR; errors for duplicates and bad fields."""
    items: dict[str, dict] = {}
    errors: list[str] = []
    for key, prefix, _parent in LEVELS:
        for item in spec.get(key, []):
            iid = str(item.get("id", ""))
            if not re.fullmatch(rf"{prefix}-\d+", iid):
                errors.append(f"{key}: bad ID {iid!r}")
                continue
            if iid in items:
                errors.append(f"{iid}: ID appears twice")
            if item.get("status") not in STATUSES:
                errors.append(f"{iid}: status must be current or removed")
            items[iid] = item
            for row in item.get("acceptance", []) if prefix == "FR" else []:
                if not str(row).startswith(iid + "."):
                    errors.append(f"{iid}: acceptance row {row} does not belong to it")
                if row in items:
                    errors.append(f"{row}: ID appears twice")
                items[row] = {"id": row, "status": item.get("status"), "row_of": iid}
    return items, errors


def check_spec(spec: dict) -> list[str]:
    items, errors = index(spec)
    for key, prefix, parent in LEVELS:
        for item in spec.get(key, []):
            iid = item.get("id")
            parents = item.get("parents", [])
            if parent and not parents and item.get("status") == "current":
                errors.append(f"{iid}: no parent {parent}")
            for pid in parents:
                p = items.get(pid)
                if p is None:
                    errors.append(f"{iid}: parent {pid} does not exist")
                elif not re.fullmatch(rf"{parent}-\d+", str(pid)):
                    errors.append(f"{iid}: parent {pid} is not a {parent}")
                elif item.get("status") == "current" and p.get("status") != "current":
                    errors.append(f"{iid}: current, but its parent {pid} is removed")
    return errors


def citations(text: str, java: bool) -> set[str]:
    found = {m.group(0) for m in CITE.finditer(text)}
    if java:
        found |= {f"FR-{a}" + (f".{b}" if b else "") for a, b in METHOD.findall(text)}
    return found


def check_citations(spec: dict, sources: dict[str, str]) -> list[str]:
    """sources: a label (a path or "commit <sha>") to its text. Java test files also cite by method name."""
    items, _ = index(spec)
    errors = []
    for label, text in sorted(sources.items()):
        for cid in sorted(citations(text, label.endswith(".java"))):
            item = items.get(cid)
            if item is None:
                errors.append(f"{label}: cites {cid}, which does not exist")
            elif item.get("status") != "current":
                errors.append(f"{label}: cites {cid}, which is removed")
    return errors


def must_without_tests(spec: dict) -> list[str]:
    return [f["id"] for f in spec.get("fr", [])
            if f.get("status") == "current" and f.get("priority") == "Must" and not f.get("tests")]


def tested_but_unlisted(spec: dict, test_sources: dict[str, str]) -> list[str]:
    cited = set()
    for label, text in test_sources.items():
        cited |= {c.split(".")[0] for c in citations(text, label.endswith(".java")) if c.startswith("FR-")}
    listed = {f["id"] for f in spec.get("fr", []) if f.get("tests")}
    known = {f["id"] for f in spec.get("fr", []) if f.get("status") == "current"}
    return sorted((cited & known) - listed, key=lambda i: int(i.split("-")[1]))


def git(root: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
                            "-C", str(root), *args], capture_output=True, text=True, timeout=20,
                           check=False, stdin=subprocess.DEVNULL,
                           env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def read_tree(root: Path, folder: str, suffixes: tuple[str, ...], skip: set[str]) -> dict[str, str]:
    out = {}
    base = root / folder
    if base.is_dir():
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.suffix in suffixes and p.name not in skip:
                out[str(p.relative_to(root))] = p.read_text(encoding="utf-8", errors="replace")
    return out


def commit_messages(root: Path, base: str) -> dict[str, str] | None:
    log = git(root, "log", f"{base}..HEAD", "--format=%h%x00%B%x01")
    if log is None:
        return None
    out = {}
    for entry in log.split("\x01"):
        sha, _, body = entry.strip("\n").partition("\x00")
        if sha:
            out[f"commit {sha}"] = body
    return out


def run(root: Path, base: str) -> int:
    path = root / SPEC
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"spec-check: cannot read {SPEC}: {e}")
        return 1
    tests = read_tree(root, "src/test", (".java",), set())
    docs = {**read_tree(root, ".claude/docs/designs", (".md",), {"README.md"}),
            **read_tree(root, ".claude/docs/plans", (".md",), {"README.md"})}
    commits = commit_messages(root, base)
    errors = check_spec(spec) + check_citations(spec, {**tests, **docs, **(commits or {})})
    fr = spec.get("fr", [])
    current = [f for f in fr if f.get("status") == "current"]
    rows = sum(len(f.get("acceptance", [])) for f in current)
    print(f"spec-check: {len(spec.get('br', []))} BR, {len(spec.get('pr', []))} PR, {len(fr)} FR "
          f"({len(current)} current, {rows} acceptance rows), {len(spec.get('tr', []))} TR")
    print(f"Sources read: {len(tests)} test files, {len(docs)} designs and plans, "
          + (f"{len(commits)} commits in {base}..HEAD" if commits is not None
             else f"commit messages not checked (git log {base}..HEAD failed)"))
    for e in errors:
        print(f"ERROR {e}")
    missing = must_without_tests(spec)
    print(f"Must FRs with no test ({len(missing)}): {', '.join(missing) or 'none'}")
    unlisted = tested_but_unlisted(spec, tests)
    if unlisted:
        print(f"Tests cite these FRs but spec.json lists no test for them: {', '.join(unlisted)}")
    print(f"Errors: {len(errors)}")
    return 1 if errors else 0


def self_test() -> int:
    good = {
        "br": [{"id": "BR-1", "status": "current", "parents": []},
               {"id": "BR-2", "status": "removed", "parents": []}],
        "pr": [{"id": "PR-1", "status": "current", "parents": ["BR-1"], "priority": "Must"}],
        "fr": [{"id": "FR-1", "status": "current", "parents": ["PR-1"], "priority": "Must",
                "acceptance": ["FR-1.1", "FR-1.2"], "tests": ["FR-1.1: owner gets it"]},
               {"id": "FR-2", "status": "current", "parents": ["PR-1"], "priority": "Must",
                "acceptance": ["FR-2.1"], "tests": []},
               {"id": "FR-3", "status": "removed", "parents": ["PR-1"], "priority": "Must",
                "acceptance": [], "tests": []}],
        "tr": [{"id": "TR-1", "status": "current", "parents": ["FR-1"]}],
    }
    bad = json.loads(json.dumps(good))
    bad["br"].append({"id": "BR-1", "status": "current", "parents": []})
    bad["pr"].append({"id": "PR-2", "status": "current", "parents": ["BR-2"]})
    bad["pr"].append({"id": "PR-3", "status": "current", "parents": ["BR-9"]})
    bad["fr"].append({"id": "FR-4", "status": "current", "parents": ["BR-1"], "acceptance": ["FR-5.1"]})
    bad["tr"].append({"id": "TR-2", "status": "current", "parents": []})
    sources = {"src/test/ATest.java": '@DisplayName("FR-1.2: x") void fr2_1_owner() {} void fr9_x() {}',
               "plan.md": "Task 1: FR-1, FR-1.3, TR-1, TR-7, FR-3", "commit abc": "Code: x (FR-2)"}
    cases = [
        ("good spec has no error", check_spec(good) == []),
        ("duplicate ID", "BR-1: ID appears twice" in check_spec(bad)),
        ("current item with a removed parent",
         "PR-2: current, but its parent BR-2 is removed" in check_spec(bad)),
        ("missing parent", "PR-3: parent BR-9 does not exist" in check_spec(bad)),
        ("parent of the wrong level", "FR-4: parent BR-1 is not a PR" in check_spec(bad)),
        ("acceptance row of another FR", "FR-4: acceptance row FR-5.1 does not belong to it" in check_spec(bad)),
        ("TR with no parent", "TR-2: no parent FR" in check_spec(bad)),
        ("bad status", any("status" in e for e in check_spec({"br": [{"id": "BR-1", "status": "old"}]}))),
        ("citations: unknown acceptance row",
         "plan.md: cites FR-1.3, which does not exist" in check_citations(good, sources)),
        ("citations: unknown TR", "plan.md: cites TR-7, which does not exist" in check_citations(good, sources)),
        ("citations: removed FR", "plan.md: cites FR-3, which is removed" in check_citations(good, sources)),
        ("citations: method name form",
         "src/test/ATest.java: cites FR-9, which does not exist" in check_citations(good, sources)),
        ("citations: known IDs pass", not any(x in e for e in check_citations(good, sources)
                                              for x in ("FR-1.2,", "FR-2.1,", "FR-2,", "TR-1,"))),
        ("Must FRs with no test", must_without_tests(good) == ["FR-2"]),
        ("tested but not listed",
         tested_but_unlisted(good, {k: v for k, v in sources.items() if k.endswith(".java")}) == ["FR-2"]),
    ]
    failed = 0
    for label, ok in cases:
        failed += not ok
        print(f"{'ok ' if ok else 'BAD'} {label}")
    print(f"\n{len(cases) - failed} of {len(cases)} passed")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Check spec.json and the IDs the work cites.")
    ap.add_argument("--self-test", action="store_true", help="run the built-in cases")
    ap.add_argument("--base", default="main", help="branch base for commit messages (default main)")
    ap.add_argument("--root", default=str(ROOT), help="repository root (default: this script's repo)")
    args = ap.parse_args()
    return self_test() if args.self_test else run(Path(args.root), args.base)


if __name__ == "__main__":
    sys.exit(main())
