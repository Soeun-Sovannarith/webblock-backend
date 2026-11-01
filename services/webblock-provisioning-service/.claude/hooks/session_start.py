#!/usr/bin/env python3
"""Short context at the start of a session (SessionStart hook).

Prints, as plain text that Claude Code adds to the context: the current branch, whether the working
tree is clean, one line of spec status (FRs by status and current FRs with no test, from
.claude/docs/requirements/spec.yaml read by scripts/py/spec_check.py) with the constitution's version,
and the files to read first. No secrets, no network, no file names from the tree. At most 14 lines.
It never blocks: any error still prints the read-first list and exits 0.
Python standard library only. Run as: python3 -I .claude/hooks/session_start.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

GIT_ENV = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
SPEC = Path(".claude/docs/requirements/spec.yaml")
CHECKER = Path("scripts/py/spec_check.py")
CONSTITUTION = Path(".claude/docs/CONSTITUTION.md")
READ_FIRST = ("Read first: .claude/CLAUDE.md Start here, .claude/WORKFLOW.md, "
              "ai/handoffs/after-phase-c.md,\n  spec in .claude/docs/requirements/.")


def git(cwd: Path, *args: str) -> str | None:
    """Read-only git, no shell; fsmonitor and hooks off, so repository config cannot run a program."""
    try:
        r = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
                            "-C", str(cwd), *args], capture_output=True, text=True, timeout=3,
                           check=False, env=GIT_ENV, stdin=subprocess.DEVNULL)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def spec_status(cwd: Path) -> str:
    """One line: FRs by status and current FRs with no test, then the constitution's version."""
    try:
        text = (cwd / CONSTITUTION).read_text(encoding="utf-8")
        found = re.search(r"^Version ([0-9]+)$", text, re.MULTILINE)
        constitution = f"version {found.group(1)}" if found else "no version line"
    except (OSError, UnicodeDecodeError):
        constitution = "not found"
    try:
        spec = importlib.util.spec_from_file_location("spec_check", cwd / CHECKER)
        if spec is None or spec.loader is None:
            raise OSError("no checker")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        register = module.load_register((cwd / SPEC).read_text(encoding="utf-8"))
        frs = [i for i in register.items.values() if i.kind == "FR"]
        current = [i for i in frs if i.current]
        untested = sum(not i.tests for i in current)
        status = (f"FR {len(current)} current, {len(frs) - len(current)} removed; {untested} current "
                  f"{'FR' if untested == 1 else 'FRs'} with no test (make spec-check)")
    except Exception:  # noqa: BLE001 - context is a help, never a blocker
        status = f"{SPEC} could not be read"
    return f"Spec: {status}. Constitution: {constitution}."


def context(cwd: Path) -> list[str]:
    branch = (git(cwd, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    if branch == "HEAD":
        branch = "detached at " + (git(cwd, "rev-parse", "--short", "HEAD") or "?").strip()
    status = git(cwd, "status", "--porcelain=v1", "-z", "--ignore-submodules")
    if status is None:
        tree = "unknown (git status failed)"
    else:
        parts, entries = iter(status.split("\0")), []
        for e in parts:
            if e:
                entries.append(e)
                if e[:1] in {"R", "C"}:
                    next(parts, None)  # a rename or copy is followed by its old path
        untracked = sum(e.startswith("??") for e in entries)
        changed = len(entries) - untracked
        tree = "clean" if not entries else f"not clean ({changed} changed, {untracked} untracked)"
    return [
        "Provisioning session context (SessionStart hook).",
        f"Branch: {branch or 'unknown'}",
        f"Working tree: {tree}",
        spec_status(cwd),
        READ_FIRST,
    ]


def main() -> int:
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or ""
    try:
        event = json.loads(sys.stdin.read() or "{}")
        if not cwd and isinstance(event, dict):
            cwd = str(event.get("cwd") or "")
    except (ValueError, OSError):
        pass
    try:
        lines = context(Path(cwd or os.getcwd()))
    except Exception:  # noqa: BLE001 - context is a help, never a blocker
        lines = ["Provisioning session context (SessionStart hook).", "Branch: unknown",
                 "Working tree: unknown", READ_FIRST]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
