#!/usr/bin/env python3
"""Quick feedback after Edit/Write (PostToolUse hook). Never blocks; it tells Claude what to fix.

Checks only the text that was just written: em and en dashes, lines over 120 characters (docs and
config), images without a digest, dependency edits (14-day rule), and ruff on the edited .py file.
Python standard library only. Run as: python3 -I .claude/hooks/post_edit_check.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

TEXT_SUFFIXES = {".md", ".toml", ".yaml", ".yml", ".sh", ".sql", ".mk", ".txt", ".py", ".proto"}
LONG_LINE_SUFFIXES = {".md", ".toml", ".yaml", ".yml", ".sh", ".sql", ".mk", ".txt"}
IMAGE_RE = re.compile(r"""^\s*(?:-\s*)?(?:image:|FROM\s+(?:--\S+\s+)*)\s*["']?([^"'\s#]+)""")


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
    if path.suffix not in TEXT_SUFFIXES and path.name not in {"Makefile", "Dockerfile"}:
        return 0
    edits = ti.get("edits") if isinstance(ti.get("edits"), list) else []
    text = str(ti.get("content", "")) + str(ti.get("new_string", "")) + "\n".join(
        str(e.get("new_string", "")) for e in edits if isinstance(e, dict))
    notes: list[str] = []

    dashes = [n for n, line in enumerate(text.splitlines(), 1) if "\u2014" in line or "\u2013"
              in line]
    if dashes:
        notes.append(f"Em or en dash in the new text (lines {dashes[:5]} of the edit). The owner's "
                     "rule: never use them; write a comma, colon or 'to'.")
    if path.suffix in LONG_LINE_SUFFIXES or path.name in {"Makefile", "Dockerfile"}:
        long_lines = [n for n, line in enumerate(text.splitlines(), 1) if len(line) > 120
                      and "http" not in line and not line.lstrip().startswith("|")]
        if long_lines:
            notes.append(f"{len(long_lines)} new line(s) over 120 characters (lines "
                         f"{long_lines[:5]} of the edit). Wrap them.")
    if path.name == "Dockerfile" or "/deploy/" in str(path) or path.name == "versions.sh":
        loose = [m.group(1) for line in text.splitlines() if (m := IMAGE_RE.match(line))
                 and "@sha256:" not in m.group(1) and "$" not in m.group(1)
                 and "{{" not in m.group(1)]
        if loose:
            notes.append(f"Image(s) without a digest: {loose[:3]}. Pin image@sha256:..., at least "
                         "14 days old, open-source licence.")
    if path.name in {"pyproject.toml", "uv.lock", "versions.sh", "tools.sh"}:
        notes.append("Dependency or tool pin changed: run make py-age (14-day rule) and "
                     "make py-vuln; keep exclude-newer in pyproject.toml.")
    ruff = project / ".venv" / "bin" / "ruff"
    if path.suffix == ".py" and ruff.exists() and "/.claude/" not in str(path) and path.exists():
        for args in (["format", "--check", "--quiet"], ["check", "--quiet",
                                                         "--output-format=concise"]):
            try:
                r = subprocess.run([str(ruff), *args, "--force-exclude", str(path)], cwd=project,
                                   capture_output=True, text=True, timeout=10, check=False)
            except (OSError, subprocess.SubprocessError):
                break
            if r.returncode != 0:
                out = (r.stdout + r.stderr).strip().splitlines()[:15]
                notes.append(f"ruff {args[0]}: " + (" | ".join(out) or "would reformat"))
    if notes:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": f"Checks on {path.name}: " + " ".join(notes),
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
