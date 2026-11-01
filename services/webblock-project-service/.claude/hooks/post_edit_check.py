#!/usr/bin/env python3
"""Quick feedback after Edit/Write (PostToolUse hook). Never blocks; it tells Claude what to fix.

Checks only the text that was just written: em and en dashes and lines over 120 characters in docs and
config files, a reminder to run ./gradlew spotlessCheck after a Java edit, and the dependency rules after
a build.gradle, version catalog or lock file edit. Java formatting itself is Spotless's job.
Python standard library only. Run as: python3 -I .claude/hooks/post_edit_check.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DOC_SUFFIXES = {".md", ".yaml", ".yml", ".sql", ".sh", ".toml", ".properties", ".txt"}
DEPENDENCY_FILES = {"build.gradle", "settings.gradle", "libs.versions.toml", "gradle.lockfile",
                    "verification-metadata.xml"}


def notes_for(path: Path, text: str) -> list[str]:
    notes: list[str] = []
    lines = text.splitlines()
    if path.suffix in DOC_SUFFIXES:
        dashes = [n for n, line in enumerate(lines, 1) if "\u2014" in line or "\u2013" in line]
        if dashes:
            notes.append(f"Em or en dash in the new text (lines {dashes[:5]} of the edit). The owner's "
                         "rule: never use them; write a comma, colon or 'to'.")
        long_lines = [n for n, line in enumerate(lines, 1) if len(line) > 120
                      and "http" not in line and not line.lstrip().startswith("|")]
        if long_lines:
            notes.append(f"{len(long_lines)} new line(s) over 120 characters (lines "
                         f"{long_lines[:5]} of the edit). Wrap them.")
    if path.suffix == ".java":
        notes.append("Java changed: run ./gradlew spotlessCheck (spotlessApply fixes the format).")
    if path.name in DEPENDENCY_FILES:
        notes.append("Build or dependency file changed: every new or changed pin needs an open-source "
                     "licence, an age of at least 14 days and an exact version; refresh the lock with "
                     "the owner's OK and run scripts/osv-scan.sh.")
    return notes


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(event, dict) or not isinstance(event.get("tool_input") or {}, dict):
        return 0
    ti = event.get("tool_input") or {}
    path = Path(str(ti.get("file_path") or ""))
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or ".")
    if not path.name or not str(path).startswith(str(project).rstrip("/") + "/"):
        return 0
    edits = ti.get("edits") if isinstance(ti.get("edits"), list) else []
    text = str(ti.get("content", "")) + str(ti.get("new_string", "")) + "\n".join(
        str(e.get("new_string", "")) for e in edits if isinstance(e, dict))
    notes = notes_for(path, text)
    if notes:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": f"Checks on {path.name}: " + " ".join(notes),
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
