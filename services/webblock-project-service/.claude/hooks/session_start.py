#!/usr/bin/env python3
"""Short context at the start of a session (SessionStart hook).

Prints, as plain text that Claude Code adds to the context: the current branch, whether the working
tree is clean, one line of spec status (FR counts and FRs with no test from spec.json, and the
constitution version), and the files to read first. No secrets, no network, no file names from the tree.
It never blocks: any error still prints the read-first list and exits 0.
Python standard library only. Run as: python3 -I .claude/hooks/session_start.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

GIT_ENV = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
TITLE = "Project service session context (SessionStart hook)."
READ_FIRST = ("Read first: .claude/CLAUDE.md (start here), .claude/WORKFLOW.md, ai/handoffs/next.md,\n"
              "  spec in .claude/docs/requirements/.")
SPEC = Path(".claude/docs/requirements/spec.json")
CONSTITUTION = Path(".claude/docs/CONSTITUTION.md")


def git(cwd: Path, *args: str) -> str | None:
    """Read-only git, no shell; fsmonitor and hooks off, so repository config cannot run a program."""
    try:
        r = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
                            "-C", str(cwd), *args], capture_output=True, text=True, timeout=3,
                           check=False, env=GIT_ENV, stdin=subprocess.DEVNULL)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def spec_line(cwd: Path) -> str:
    """One line: FR counts and FRs with no test from spec.json, and the constitution version."""
    try:
        spec = json.loads((cwd / SPEC).read_text(encoding="utf-8"))
        frs = [f for f in spec.get("fr", []) if isinstance(f, dict)]
        current = [f for f in frs if f.get("status") == "current"]
        untested = sum(not f.get("tests") for f in current)
        must = sum(not f.get("tests") and f.get("priority") == "Must" for f in current)
        spec_text = (f"{len(frs)} FRs ({len(current)} current), {untested} current with no test "
                     f"({must} Must)")
    except (OSError, ValueError, AttributeError, TypeError):
        spec_text = f"{SPEC} unreadable"
    version = "unknown"
    try:
        for line in (cwd / CONSTITUTION).read_text(encoding="utf-8").splitlines()[:10]:
            if line.startswith("Version "):
                version = line.split(" ", 1)[1].strip()[:10]
                break
    except OSError:
        pass
    return f"Spec: {spec_text}. Constitution: version {version}."


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
        TITLE,
        f"Branch: {branch or 'unknown'}",
        f"Working tree: {tree}",
        spec_line(cwd),
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
        lines = [TITLE, "Branch: unknown",
                 "Working tree: unknown", READ_FIRST]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
