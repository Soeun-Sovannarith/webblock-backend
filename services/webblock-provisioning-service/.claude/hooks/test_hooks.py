#!/usr/bin/env python3
"""Table test for the commit ID check (guard.py), reviewer_writes.py and session_start.py.

Run: python3 -I .claude/hooks/test_hooks.py   (exit 0 = all pass)

It builds a throw-away git repository in a temporary folder, so it never touches the real repository,
a cluster, or a secret file. The cases make no commit; each one only sets up the index.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HOOKS = Path(__file__).parent
GUARD = HOOKS / "guard.py"
REVIEWER = HOOKS / "reviewer_writes.py"
SESSION = HOOKS / "session_start.py"
REVIEWER_AGENTS = ("provisioning-validator", "final-reviewer", "security-reviewer", "db-notion-auditor",
                   "qa-reviewer")
SETTINGS_FLAG = "--if-reviewer-agent"
NO_AGENT = object()  # leave agent_type out of the input, as in the main session

ALLOW, ASK, DENY = None, "ask", "deny"
RAW = "__raw__"


def hook(script: Path, tool: str, tool_input: object, cwd: Path, agent: object = "security-reviewer",
         args: tuple[str, ...] = ()) -> str | None:
    if tool == RAW:
        stdin = str(tool_input)
    else:
        event = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
                 "cwd": str(cwd), "session_id": "s", "permission_mode": "default"}
        if agent is not NO_AGENT:
            event.update(agent_type=agent, agent_id="a1")
        stdin = json.dumps(event)
    r = subprocess.run([sys.executable, "-I", str(script), *args], input=stdin, text=True, cwd=cwd,
                       capture_output=True, timeout=14, check=False,
                       env={**os.environ, "CLAUDE_PROJECT_DIR": str(cwd)})
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    if not r.stdout.strip():
        return None
    return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"]


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def setup(tmp: Path) -> Path:
    repo = tmp / "repo"
    for d in ("src/migrations", "tests", "deploy", "docs", ".claude/rules", "ai/reviews"):
        (repo / d).mkdir(parents=True)
    git(repo, "init", "-q", "-b", "feature-x")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    files = {"src/a.py": "x = 1\n", "src/migrations/0001.sql": "-- up\n", "tests/test_a.py": "",
             "deploy/x.yaml": "a: 1\n", "docs/x.md": "doc\n", ".claude/rules/r.md": "rule\n"}
    for name, text in files.items():
        (repo / name).write_text(text)
    git(repo, "add", *files)
    git(repo, "commit", "-qm", "init")
    for name in files:  # every file now has an unstaged change, so `git add <file>` stages it
        (repo / name).write_text((repo / name).read_text() + "# change\n")
    (repo / "msg-id.txt").write_text("Code: change (FR-4)\n")
    (repo / "msg-none.txt").write_text("Code: change\n")
    return repo


def stage(repo: Path, paths: list[str]) -> None:
    git(repo, "reset", "-q")
    if paths:
        git(repo, "add", *paths)


HEREDOC = "git commit -m \"$(cat <<'EOF'\nCode: change {id}\n\nDon't push.\nEOF\n)\""


def commit_cases() -> list[tuple[list[str], str, str | None]]:
    return [
        (["src/a.py"], "git commit -m 'Code: change'", ASK),
        (["src/a.py"], "git commit -m 'Code: change (FR-12)'", ALLOW),
        (["docs/x.md"], "git commit -m 'Docs: change'", ALLOW),
        ([".claude/rules/r.md"], "git commit -m 'Rules: change'", ALLOW),
        (["src/a.py"], HEREDOC.format(id="(TR-3)"), ALLOW),
        (["src/a.py"], HEREDOC.format(id=""), ASK),
        (["tests/test_a.py"], "git commit -m 'Tests: change'", ASK),
        (["deploy/x.yaml"], "git commit -m 'Chart: change'", ASK),
        (["src/migrations/0001.sql"], "git commit -m 'DB: change'", ASK),
        (["src/migrations/0001.sql"], "git commit -m 'DB: change (TR-40, FR-2)'", ALLOW),
        (["docs/x.md", "src/a.py"], "git commit -m 'Mixed'", ASK),
        (["src/a.py"], "git commit -m 'Code: change' -m 'Refs FR-9'", ALLOW),
        (["src/a.py"], "git commit --message='Code: change (FR-7)'", ALLOW),
        (["src/a.py"], "git commit -qm 'Code: change (FR-7)'", ALLOW),
        (["src/a.py"], "git commit -m 'Code: change (fr-12)'", ASK),
        (["src/a.py"], "git commit -m 'Code: change (FR-)'", ASK),
        (["src/a.py"], "git commit -F msg-id.txt", ALLOW),
        (["src/a.py"], "git commit -F msg-none.txt", ASK),
        (["src/a.py"], "git commit --file=msg-id.txt", ALLOW),
        (["src/a.py"], "git commit -F - <<'EOF'\nCode: change (FR-5)\nEOF", ALLOW),
        (["src/a.py"], "git commit", ASK),
        (["src/a.py"], "git commit --dry-run -m x", ALLOW),
        ([], "git add src/a.py && git commit -m 'Code: change'", ASK),
        ([], "git add docs/x.md && git commit -m 'Docs: change'", ALLOW),
        ([], "git add docs/x.md src/a.py && git commit -m 'Code: change (FR-1)'", ALLOW),
        ([], "git add -u && git commit -m 'All'", ASK),
        ([], "git commit -am 'All'", DENY),  # git commit -a is denied always, ID or not
        ([], "git commit -am 'Code: change (FR-1)'", DENY),
        ([], "git commit -m 'Code: change' src/a.py", ASK),
        ([], "git commit -m 'Docs: change' docs/x.md", ALLOW),
        ([], "git rm -q --cached src/a.py && git commit -m 'Remove'", ASK),
        (["src/a.py"], "cd docs && git commit -m 'Code: change'", ASK),
        (["src/a.py"], "bash -c \"git commit -m 'Code: change'\"", ASK),
        ([], "git commit -m 'Nothing staged'", ALLOW),
    ]


def reviewer_cases(repo: Path) -> list[tuple[str, object, str | None]]:
    b = lambda c: ("Bash", {"command": c})  # noqa: E731
    return [
        ("Write", {"file_path": str(repo / "ai/reviews/x.md"), "content": "r"}, ALLOW),
        ("Write", {"file_path": "ai/reviews/sub/y.md", "content": "r"}, ALLOW),
        ("Write", {"file_path": str(repo / "src/a.py"), "content": "x"}, DENY),
        ("Write", {"file_path": str(repo / "ai/reviews/../../src/a.py"), "content": "x"}, DENY),
        ("Write", {"file_path": str(repo / "ai/reviews-old/x.md"), "content": "x"}, DENY),
        ("Edit", {"file_path": str(repo / "docs/x.md"), "old_string": "a", "new_string": "b"}, DENY),
        ("Edit", {"file_path": str(repo / "ai/reviews/x.md"), "old_string": "a", "new_string": "b"},
         ALLOW),
        ("NotebookEdit", {"notebook_path": str(repo / "n.ipynb"), "new_source": "x"}, DENY),
        ("Write", {"content": "x"}, DENY),
        (RAW, "not json", DENY),
        (RAW, json.dumps({"tool_name": "Bash", "tool_input": {"command": 5}}), DENY),
        # shell: read-only stays allowed
        (*b("pytest -q tests/unit"), ALLOW),
        (*b(".venv/bin/python -X dev -m pytest tests/unit -k fr12"), ALLOW),
        (*b("make py-test py-lint"), ALLOW),
        (*b("make contracts-check chart-check"), ALLOW),
        (*b("make qa-sweep"), ALLOW),
        (*b("make qa-sweep 2>&1 | tail -40"), ALLOW),
        (*b("make qa-sweep py-test"), ALLOW),
        (*b("grep -rn 'FR-12' src tests | head -20"), ALLOW),
        (*b("git log --oneline -10"), ALLOW),
        (*b("git diff abc123...def456 -- src/"), ALLOW),
        (*b("git show HEAD:src/a.py"), ALLOW),
        (*b("git status --short"), ALLOW),
        (*b("git branch --show-current"), ALLOW),
        (*b("git -C . rev-parse HEAD"), ALLOW),
        (*b("git worktree add --detach .claude/worktrees/review HEAD"), ALLOW),
        (*b("sed -n 1,20p src/a.py"), ALLOW),
        (*b("ruff check src"), ALLOW),
        (*b("ruff format --check src"), ALLOW),
        (*b("uv run pytest -q"), ALLOW),
        (*b("pytest -q > /dev/null 2>&1; echo $?"), ALLOW),
        (*b("echo report > ai/reviews/r.md"), ALLOW),
        (*b("cat <<'EOF' > ai/reviews/r.md\n# Review\nEOF"), ALLOW),
        (*b("git diff HEAD~1 | tee ai/reviews/diff.txt"), ALLOW),
        (*b("mkdir -p ai/reviews/phase-d"), ALLOW),
        # shell: writes and git state changes are denied
        (*b("git commit -m 'Review (FR-1)'"), DENY),
        (*b("git add ai/reviews/x.md"), DENY),
        (*b("git checkout other"), DENY),
        (*b("git switch other"), DENY),
        (*b("git stash"), DENY),
        (*b("git branch -D other"), DENY),
        (*b("git reset --soft HEAD~1"), DENY),
        (*b("git diff --output=src/x.patch"), DENY),
        (*b("git worktree add -b review /tmp/review"), DENY),
        (*b("echo x > src/a.py"), DENY),
        (*b("echo x >> docs/x.md"), DENY),
        (*b("pytest -q > report.txt"), DENY),
        (*b("rm ai/reviews/old.md"), DENY),
        (*b("rm -rf src"), DENY),
        (*b("mv src/a.py src/b.py"), DENY),
        (*b("cp src/a.py src/b.py"), DENY),
        (*b("touch src/new.py"), DENY),
        (*b("sed -i s/1/2/ src/a.py"), DENY),
        (*b("perl -pi -e s/1/2/ src/a.py"), DENY),
        (*b("tee src/a.py < /dev/null"), DENY),
        (*b("find . -name '*.pyc' -delete"), DENY),
        (*b("ruff format src"), DENY),
        (*b("ruff check --fix src"), DENY),
        (*b("uv lock"), DENY),
        (*b("pip install x"), DENY),
        (*b("make contracts-sync"), DENY),
        (*b("make migrate-up"), DENY),
        (*b("make qa-sweep-fix"), DENY),
        (*b("make -C other qa-sweep"), DENY),
        (*b("bash -c 'git commit -m x'"), DENY),
        (*b("echo $(git stash)"), DENY),
        (*b("pytest && git add -u"), DENY),
        (*b("kubectl get pods"), DENY),
        (*b("echo 'unclosed"), DENY),
        (*b("cd src && echo x > a.py"), DENY),
    ]


def settings_hook_cases(repo: Path) -> list[tuple[object, str, object, str | None]]:
    """(agent_type, tool, tool_input, want) for reviewer_writes.py run from settings.json.

    Settings hooks also run in an untrusted `claude -p` session, where frontmatter hooks do not, so the
    limit must follow agent_type there. Agents that are not reviewers keep the normal flow.
    """
    touch = ("Bash", {"command": "touch src/x.py"})
    sweep = ("Bash", {"command": "make qa-sweep"})
    write_src = ("Write", {"file_path": str(repo / "src/x.py"), "content": "x"})
    write_review = ("Write", {"file_path": str(repo / "ai/reviews/qa-x.md"), "content": "r"})
    edit_src = ("Edit", {"file_path": str(repo / "src/a.py"), "old_string": "1", "new_string": "2"})
    commit = ("Bash", {"command": "git add -u && git commit -m 'Review (TR-2)'"})
    cases: list[tuple[object, str, object, str | None]] = []
    for name in REVIEWER_AGENTS:
        cases += [(name, *touch, DENY), (name, *write_src, DENY), (name, *edit_src, DENY),
                  (name, *commit, DENY), (name, *sweep, ALLOW), (name, *write_review, ALLOW),
                  (name, "Bash", {"command": "git log --oneline -3"}, ALLOW)]
    for other in (NO_AGENT, None, "", "implementer", "Explore", "general-purpose", "kind-runner",
                  "qa-reviewer-old", "security-reviewer "):
        cases += [(other, *touch, ALLOW), (other, *write_src, ALLOW)]
    cases += [
        (5, *touch, DENY),
        (["security-reviewer"], *touch, DENY),
        (NO_AGENT, RAW, "not json", DENY),
        (NO_AGENT, RAW, json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"},
                                    "agent_type": "qa-reviewer"}), ALLOW),
        (NO_AGENT, RAW, json.dumps({"tool_name": "Bash", "tool_input": {"command": 5},
                                    "agent_type": "qa-reviewer"}), DENY),
    ]
    return cases


SPEC_YAML = """version: 1
items:
  - id: PR-1
    title: "Offer"
    status: current
    parents: []
  - id: FR-1
    title: "Tested"
    status: current
    parents: [PR-1]
    rows: [FR-1.1]
    tests: [tests/unit/test_a.py::test_fr1_x]
  - id: FR-2
    title: "Untested"
    status: current
    parents: [PR-1]
    rows: []
    tests: []
  - id: FR-3
    title: "Removed"
    status: removed
    parents: [PR-1]
    rows: []
    tests: []
"""


def run_session(repo: Path, stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-I", str(SESSION)], input=stdin, text=True, cwd=repo,
                          capture_output=True, timeout=14, check=False,
                          env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo)})


def spec_session_rows(repo: Path) -> list[tuple[str, bool]]:
    """The spec line, before and after the register, the checker and the constitution exist."""
    start = json.dumps({"hook_event_name": "SessionStart", "source": "startup", "cwd": str(repo)})
    before = run_session(repo, start).stdout
    (repo / ".claude/docs/requirements").mkdir(parents=True, exist_ok=True)
    (repo / ".claude/docs/requirements/spec.yaml").write_text(SPEC_YAML)
    (repo / ".claude/docs/CONSTITUTION.md").write_text("# Constitution\n\nVersion 3\n")
    (repo / "scripts/py").mkdir(parents=True, exist_ok=True)
    (repo / "scripts/py/spec_check.py").write_text(
        (HOOKS.parent.parent / "scripts/py/spec_check.py").read_text())
    after = run_session(repo, start)
    lines = after.stdout.splitlines()
    return [
        ("session start (no register): a spec line that says it could not be read",
         "Spec: .claude/docs/requirements/spec.yaml could not be read" in before
         and "Constitution: not found" in before),
        ("session start (register): exit 0", after.returncode == 0),
        ("session start (register): FRs by status and FRs with no test",
         "Spec: FR 2 current, 1 removed; 1 current FR with no test" in after.stdout),
        ("session start (register): the constitution version", "Constitution: version 3." in after.stdout),
        ("session start (register): 3 to 14 lines, each at most 120 characters",
         3 <= len(lines) < 15 and all(len(x) <= 120 for x in lines)),
    ]


def session_rows(repo: Path) -> list[tuple[str, bool]]:
    rows = []
    for label, stdin in (("startup", json.dumps({"hook_event_name": "SessionStart",
                                                 "source": "startup", "cwd": str(repo)})),
                         ("bad stdin", "not json")):
        r = subprocess.run([sys.executable, "-I", str(SESSION)], input=stdin, text=True, cwd=repo,
                           capture_output=True, timeout=14, check=False,
                           env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo)})
        lines = r.stdout.splitlines()
        rows += [
            (f"session start ({label}): exit 0", r.returncode == 0),
            (f"session start ({label}): 3 to 14 lines", 3 <= len(lines) < 15),
            (f"session start ({label}): lines at most 120 characters",
             all(len(x) <= 120 for x in lines)),
            (f"session start ({label}): names the branch", "Branch: feature-x" in r.stdout),
            (f"session start ({label}): says whether the tree is clean",
             re.search(r"Working tree: (clean|not clean)", r.stdout) is not None),
            (f"session start ({label}): read-first list",
             all(s in r.stdout for s in (".claude/CLAUDE.md", "Start here", ".claude/WORKFLOW.md",
                                         "ai/handoffs/after-phase-c.md",
                                         ".claude/docs/requirements/"))),
            (f"session start ({label}): no dashes", "\u2014" not in r.stdout
             and "\u2013" not in r.stdout),
        ]
    return rows


def settings_rows() -> list[tuple[str, bool]]:
    claude = HOOKS.parent
    s = json.loads((claude / "settings.json").read_text())
    hooks = s.get("hooks", {})
    start = [h for e in hooks.get("SessionStart", []) for h in e.get("hooks", [])]
    pre = [h for e in hooks.get("PreToolUse", []) for h in e.get("hooks", [])]
    rows = [
        ("settings: SessionStart runs session_start.py",
         any("session_start.py" in h.get("command", "") for h in start)),
        ("settings: guard.py still blocks on failure",
         any("guard.py" in h.get("command", "") and h.get("onFailure") == "block" for h in pre)),
        ("settings: post_edit_check.py still registered",
         any("post_edit_check.py" in h.get("command", "") for e in hooks.get("PostToolUse", [])
             for h in e.get("hooks", []))),
        ("settings: git commit --amend asks the owner",
         "Bash(git commit --amend *)" in s.get("permissions", {}).get("ask", [])),
    ]
    shell_and_write = ("Bash", "Monitor", "Write", "Edit", "MultiEdit", "NotebookEdit")
    limit = [(e.get("matcher", ""), h) for e in hooks.get("PreToolUse", []) for h in e.get("hooks", [])
             if "reviewer_writes.py" in h.get("command", "")]
    rows.append(("settings: PreToolUse runs reviewer_writes.py " + SETTINGS_FLAG + ", blocks on failure",
                 len(limit) == 1 and limit[0][1].get("command", "").endswith(SETTINGS_FLAG)
                 and limit[0][1].get("onFailure") == "block"
                 and all(re.fullmatch(limit[0][0], t) for t in shell_and_write)))
    spec = importlib.util.spec_from_file_location("reviewer_writes", REVIEWER)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    rows.append(("reviewer_writes.py: REVIEWER_AGENTS is the five reviewer agents",
                 set(getattr(mod, "REVIEWER_AGENTS", ())) == set(REVIEWER_AGENTS)))
    for name in REVIEWER_AGENTS:
        text = (claude / "agents" / f"{name}.md").read_text()
        front = text.split("\n---", 1)[0]
        rows.append((f"agent {name}: frontmatter PreToolUse runs reviewer_writes.py, blocks on failure",
                     "reviewer_writes.py" in front and "onFailure: block" in front
                     and "PreToolUse" in front and re.search(r"matcher: .*Bash.*Write.*Edit", front)
                     is not None))
    return rows


def main() -> int:
    failed = total = 0

    def report(ok: bool, text: str) -> None:
        nonlocal failed, total
        total += 1
        failed += not ok
        print(f"{'ok ' if ok else 'BAD'} {text}")

    with tempfile.TemporaryDirectory() as t:
        repo = setup(Path(t))
        for staged, command, want in commit_cases():
            stage(repo, staged)
            got = hook(GUARD, "Bash", {"command": command}, repo)
            report(got == want, f"commit  want={want!s:<5} got={got!s:<5} staged={staged} "
                                f"{command[:60]!r}")
        stage(repo, [])
        for tool, ti, want in reviewer_cases(repo):
            got = hook(REVIEWER, tool, ti, repo)
            label = (ti.get("command") or ti.get("file_path") if isinstance(ti, dict) else None) \
                or str(ti)
            report(got == want, f"review  want={want!s:<5} got={got!s:<5} {tool:<12} {label[:60]!r}")
        for tool, ti, want in (("Bash", {"command": "touch src/x.py"}, DENY),
                               ("Write", {"file_path": str(repo / "src/x.py"), "content": "x"}, DENY)):
            got = hook(REVIEWER, tool, ti, repo, agent=NO_AGENT)
            report(got == want, f"frontmatter, no agent_type: want={want!s:<5} got={got!s:<5} {tool}")
        for agent, tool, ti, want in settings_hook_cases(repo):
            got = hook(REVIEWER, tool, ti, repo, agent=agent, args=(SETTINGS_FLAG,))
            label = (ti.get("command") or ti.get("file_path") if isinstance(ti, dict) else None) \
                or str(ti)
            who = "(none)" if agent is NO_AGENT else repr(agent)
            report(got == want, f"settings want={want!s:<5} got={got!s:<5} agent={who:<26} {tool:<6} "
                                f"{label[-45:]!r}")
        for label, ok in session_rows(repo):
            report(ok, label)
        for label, ok in spec_session_rows(repo):
            report(ok, label)
    for label, ok in settings_rows():
        report(ok, label)
    print(f"\n{total - failed} of {total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
