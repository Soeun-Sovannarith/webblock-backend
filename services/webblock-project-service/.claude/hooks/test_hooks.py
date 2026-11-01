#!/usr/bin/env python3
"""Table test for the commit ID check (guard.py), reviewer_writes.py, session_start.py and
post_edit_check.py.

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
POST_EDIT = HOOKS / "post_edit_check.py"
REVIEWER_AGENTS = ("project-validator", "final-reviewer", "security-reviewer", "db-notion-auditor",
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
    for d in ("src/main/java", "src/main/resources/db/migration/project", "src/test/java", "gradle",
              "docs", ".claude/rules", "ai/reviews", "scripts"):
        (repo / d).mkdir(parents=True)
    git(repo, "init", "-q", "-b", "feature-x")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    files = {"src/main/java/A.java": "class A {}\n",
             "src/main/resources/application.yml": "a: 1\n",
             "src/main/resources/db/migration/project/V6__x.sql": "-- up\n",
             "src/test/java/ATest.java": "class ATest {}\n", "build.gradle": "plugins {}\n",
             "gradle/libs.versions.toml": "[versions]\n", "settings.gradle": "x\n",
             "docs/x.md": "doc\n", ".claude/rules/r.md": "rule\n", "scripts/x.sh": "true\n"}
    for name, text in files.items():
        (repo / name).write_text(text)
    git(repo, "add", *files)
    git(repo, "commit", "-qm", "init")
    for name in files:  # every file now has an unstaged change, so `git add <file>` stages it
        (repo / name).write_text((repo / name).read_text() + "// change\n")
    spec = {"fr": [{"id": "FR-1", "status": "current", "priority": "Must", "tests": ["FR-1.1: x"]},
                   {"id": "FR-2", "status": "current", "priority": "Must", "tests": []},
                   {"id": "FR-3", "status": "current", "priority": "Should", "tests": []},
                   {"id": "FR-4", "status": "removed", "priority": "Must", "tests": []}]}
    (repo / ".claude/docs/requirements").mkdir(parents=True)
    (repo / ".claude/docs/requirements/spec.json").write_text(json.dumps(spec))
    (repo / ".claude/docs/CONSTITUTION.md").write_text("# Project constitution\n\nVersion 3\n")
    (repo / "msg-id.txt").write_text("Code: change (FR-4)\n")
    (repo / "msg-none.txt").write_text("Code: change\n")
    return repo


def stage(repo: Path, paths: list[str]) -> None:
    git(repo, "reset", "-q")
    if paths:
        git(repo, "add", *paths)


HEREDOC = "git commit -m \"$(cat <<'EOF'\nCode: change {id}\n\nDon't push.\nEOF\n)\""
A = "src/main/java/A.java"
MIGRATION = "src/main/resources/db/migration/project/V6__x.sql"


def commit_cases() -> list[tuple[list[str], str, str | None]]:
    return [
        ([A], "git commit -m 'Code: change'", ASK),
        ([A], "git commit -m 'Code: change (FR-12)'", ALLOW),
        (["docs/x.md"], "git commit -m 'Docs: change'", ALLOW),
        ([".claude/rules/r.md"], "git commit -m 'Rules: change'", ALLOW),
        (["settings.gradle"], "git commit -m 'Build: rename'", ALLOW),
        ([A], HEREDOC.format(id="(TR-3)"), ALLOW),
        ([A], HEREDOC.format(id=""), ASK),
        (["src/test/java/ATest.java"], "git commit -m 'Tests: change'", ASK),
        (["src/main/resources/application.yml"], "git commit -m 'Config: change'", ASK),
        (["build.gradle"], "git commit -m 'Build: change'", ASK),
        (["gradle/libs.versions.toml"], "git commit -m 'Deps: change'", ASK),
        (["gradle/libs.versions.toml"], "git commit -m 'Deps: bump (TR-8)'", ALLOW),
        ([MIGRATION], "git commit -m 'DB: change'", ASK),
        ([MIGRATION], "git commit -m 'DB: change (TR-40, FR-2)'", ALLOW),
        (["scripts/x.sh"], "git commit -m 'Scripts: change'", ASK),
        (["scripts/x.sh"], "git commit -m 'Scripts: change (TR-13)'", ALLOW),
        (["docs/x.md", A], "git commit -m 'Mixed'", ASK),
        ([A], "git commit -m 'Code: change' -m 'Refs FR-9'", ALLOW),
        ([A], "git commit --message='Code: change (FR-7)'", ALLOW),
        ([A], "git commit -qm 'Code: change (FR-7)'", ALLOW),
        ([A], "git commit -m 'Code: change (fr-12)'", ASK),
        ([A], "git commit -m 'Code: change (FR-)'", ASK),
        ([A], "git commit -F msg-id.txt", ALLOW),
        ([A], "git commit -F msg-none.txt", ASK),
        ([A], "git commit --file=msg-id.txt", ALLOW),
        ([A], "git commit -F - <<'EOF'\nCode: change (FR-5)\nEOF", ALLOW),
        ([A], "git commit", ASK),
        ([A], "git commit --dry-run -m x", ALLOW),
        ([], f"git add {A} && git commit -m 'Code: change'", ASK),
        ([], "git add docs/x.md && git commit -m 'Docs: change'", ALLOW),
        ([], f"git add docs/x.md {A} && git commit -m 'Code: change (FR-1)'", ALLOW),
        ([], "git add -u && git commit -m 'All'", ASK),
        ([], "git commit -am 'All'", ASK),
        ([], f"git commit -m 'Code: change' {A}", ASK),
        ([], "git commit -m 'Docs: change' docs/x.md", ALLOW),
        ([], f"git rm -q --cached {A} && git commit -m 'Remove'", ASK),
        ([A], "cd docs && git commit -m 'Code: change'", ASK),
        ([A], "bash -c \"git commit -m 'Code: change'\"", ASK),
        ([], "git commit -m 'Nothing staged'", ALLOW),
    ]


def reviewer_cases(repo: Path) -> list[tuple[str, object, str | None]]:
    b = lambda c: ("Bash", {"command": c})  # noqa: E731
    return [
        ("Write", {"file_path": str(repo / "ai/reviews/x.md"), "content": "r"}, ALLOW),
        ("Write", {"file_path": "ai/reviews/sub/y.md", "content": "r"}, ALLOW),
        ("Write", {"file_path": str(repo / A), "content": "x"}, DENY),
        ("Write", {"file_path": str(repo / "ai/reviews/../../src/main/java/A.java"), "content": "x"},
         DENY),
        ("Write", {"file_path": str(repo / "ai/reviews-old/x.md"), "content": "x"}, DENY),
        ("Edit", {"file_path": str(repo / "docs/x.md"), "old_string": "a", "new_string": "b"}, DENY),
        ("Edit", {"file_path": str(repo / "ai/reviews/x.md"), "old_string": "a", "new_string": "b"},
         ALLOW),
        ("NotebookEdit", {"notebook_path": str(repo / "n.ipynb"), "new_source": "x"}, DENY),
        ("Write", {"content": "x"}, DENY),
        (RAW, "not json", DENY),
        (RAW, json.dumps({"tool_name": "Bash", "tool_input": {"command": 5}}), DENY),
        # shell: read-only stays allowed
        (*b("./gradlew test"), ALLOW),
        (*b("./gradlew test --tests '*ProjectServiceTest*'"), ALLOW),
        (*b("./gradlew check integrationTest jacocoTestReport"), ALLOW),
        (*b("./gradlew spotlessCheck"), ALLOW),
        (*b("./gradlew compileJava compileTestJava"), ALLOW),
        (*b("./gradlew pmdMain pmdTest spotbugsMain spotbugsTest cpd"), ALLOW),
        (*b("./gradlew pmdMain spotlessApply"), DENY),
        (*b("./gradlew dependencies --configuration runtimeClasspath"), ALLOW),
        (*b("./gradlew :test --rerun-tasks --console=plain"), ALLOW),
        (*b("./gradlew check -x integrationTest"), ALLOW),
        (*b("./gradlew --version"), ALLOW),
        (*b("./gradlew check 2>&1 | tail -40"), ALLOW),
        (*b("grep -rn 'FR-12' src | head -20"), ALLOW),
        (*b("git log --oneline -10"), ALLOW),
        (*b("git diff abc123...def456 -- src/"), ALLOW),
        (*b("git show HEAD:src/main/java/A.java"), ALLOW),
        (*b("git status --short"), ALLOW),
        (*b("git branch --show-current"), ALLOW),
        (*b("git -C . rev-parse HEAD"), ALLOW),
        (*b("git worktree add --detach .claude/worktrees/review HEAD"), ALLOW),
        (*b("sed -n 1,20p src/main/java/A.java"), ALLOW),
        (*b("./gradlew test > /dev/null 2>&1; echo $?"), ALLOW),
        (*b("echo report > ai/reviews/r.md"), ALLOW),
        (*b("cat <<'EOF' > ai/reviews/r.md\n# Review\nEOF"), ALLOW),
        (*b("git diff HEAD~1 | tee ai/reviews/diff.txt"), ALLOW),
        (*b("mkdir -p ai/reviews/j3"), ALLOW),
        # shell: writes, formatting, lock rewrites and git state changes are denied
        (*b("./gradlew spotlessApply"), DENY),
        (*b("./gradlew sA"), DENY),
        (*b("./gradlew check spotlessApply"), DENY),
        (*b("./gradlew dependencies --write-locks"), DENY),
        (*b("./gradlew resolveAndLockAll --write-locks"), DENY),
        (*b("./gradlew --write-verification-metadata sha256 check"), DENY),
        (*b("./gradlew -I evil.gradle test"), DENY),
        (*b("./gradlew bootRun"), DENY),
        (*b("./gradlew clean"), DENY),
        (*b("./gradlew"), DENY),
        (*b("gradle build"), DENY),
        (*b("git commit -m 'Review (FR-1)'"), DENY),
        (*b("git add ai/reviews/x.md"), DENY),
        (*b("git checkout other"), DENY),
        (*b("git switch other"), DENY),
        (*b("git stash"), DENY),
        (*b("git branch -D other"), DENY),
        (*b("git reset --soft HEAD~1"), DENY),
        (*b("git diff --output=src/x.patch"), DENY),
        (*b("git worktree add -b review /tmp/review"), DENY),
        (*b("echo x > src/main/java/A.java"), DENY),
        (*b("echo x >> docs/x.md"), DENY),
        (*b("./gradlew test > report.txt"), DENY),
        (*b("rm ai/reviews/old.md"), DENY),
        (*b("rm -rf src"), DENY),
        (*b("mv src/main/java/A.java src/main/java/B.java"), DENY),
        (*b("cp src/main/java/A.java src/main/java/B.java"), DENY),
        (*b("touch src/main/java/New.java"), DENY),
        (*b("sed -i s/1/2/ src/main/java/A.java"), DENY),
        (*b("perl -pi -e s/1/2/ src/main/java/A.java"), DENY),
        (*b("tee src/main/java/A.java < /dev/null"), DENY),
        (*b("find . -name '*.class' -delete"), DENY),
        (*b("flyway migrate"), DENY),
        (*b("pip install x"), DENY),
        (*b("make -C ../provision-service proto-up"), DENY),
        (*b("bash -c 'git commit -m x'"), DENY),
        (*b("echo $(git stash)"), DENY),
        (*b("./gradlew test && git add -u"), DENY),
        (*b("kubectl get pods"), DENY),
        (*b("echo 'unclosed"), DENY),
        (*b("cd src && echo x > a.java"), DENY),
    ]


def settings_hook_cases(repo: Path) -> list[tuple[object, str, object, str | None]]:
    """(agent_type, tool, tool_input, want) for reviewer_writes.py run from settings.json.

    Settings hooks also run in an untrusted `claude -p` session, where frontmatter hooks do not, so the
    limit must follow agent_type there. Agents that are not reviewers keep the normal flow.
    """
    touch = ("Bash", {"command": "touch src/main/java/X.java"})
    sweep = ("Bash", {"command": "./gradlew check"})
    write_src = ("Write", {"file_path": str(repo / "src/main/java/X.java"), "content": "x"})
    write_review = ("Write", {"file_path": str(repo / "ai/reviews/qa-x.md"), "content": "r"})
    edit_src = ("Edit", {"file_path": str(repo / A), "old_string": "1", "new_string": "2"})
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
             all(s in r.stdout for s in (".claude/CLAUDE.md", ".claude/WORKFLOW.md",
                                         "ai/handoffs/next.md", ".claude/docs/requirements/"))),
            (f"session start ({label}): no dashes", "\u2014" not in r.stdout
             and "\u2013" not in r.stdout),
            (f"session start ({label}): spec status and constitution version",
             "Spec: 4 FRs (3 current), 2 current with no test (1 Must). Constitution: version 3."
             in lines),
        ]
    empty = repo.parent / "empty"
    empty.mkdir(exist_ok=True)
    r = subprocess.run([sys.executable, "-I", str(SESSION)], input="{}", text=True, cwd=empty,
                       capture_output=True, timeout=14, check=False,
                       env={**os.environ, "CLAUDE_PROJECT_DIR": str(empty)})
    rows.append(("session start (no spec.json, no constitution): exit 0, says so",
                 r.returncode == 0 and "spec.json unreadable" in r.stdout
                 and "Constitution: version unknown." in r.stdout))
    return rows


def post_edit(repo: Path, tool_input: object) -> tuple[int, str]:
    stdin = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Write",
                        "tool_input": tool_input, "cwd": str(repo)})
    r = subprocess.run([sys.executable, "-I", str(POST_EDIT)], input=stdin, text=True, cwd=repo,
                       capture_output=True, timeout=14, check=False,
                       env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo)})
    if not r.stdout.strip():
        return r.returncode, ""
    return r.returncode, json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]


def post_edit_rows(repo: Path) -> list[tuple[str, bool]]:
    """(label, ok): the hook never blocks (exit 0, no decision) and names what to fix."""
    long = "x " * 70
    cases = [
        ("md with an em dash: warns", {"file_path": str(repo / "docs/x.md"),
                                       "content": "a \u2014 b\n"}, "dash"),
        ("md with an en dash: warns", {"file_path": str(repo / "docs/x.md"),
                                       "new_string": "1\u20132\n"}, "dash"),
        ("md with a long line: warns", {"file_path": str(repo / "docs/x.md"), "content": long},
         "over 120"),
        ("md with a long table row: quiet", {"file_path": str(repo / "docs/x.md"),
                                             "content": "| " + long}, ""),
        ("plain md: quiet", {"file_path": str(repo / "docs/x.md"), "content": "Plain, short.\n"}, ""),
        ("Java edit: spotlessCheck reminder", {"file_path": str(repo / A),
                                               "new_string": "int a; // " + long}, "spotlessCheck"),
        ("Java with a dash: only the reminder", {"file_path": str(repo / A), "content": "// a \u2014 b"},
         "spotlessCheck"),
        ("version catalog: dependency rules", {"file_path": str(repo / "gradle/libs.versions.toml"),
                                               "content": "a = \"1\"\n"}, "14 days"),
        ("file outside the project: quiet", {"file_path": "/tmp/elsewhere/x.md", "content": "a \u2014"},
         ""),
    ]
    rows = []
    for label, ti, want in cases:
        code, text = post_edit(repo, ti)
        ok = code == 0 and (want in text if want else text == "") and "permissionDecision" not in text
        if label.startswith("Java with a dash"):
            ok = ok and "dash" not in text
        rows.append((f"post-edit: {label}", ok))
    code, text = post_edit(repo, "not an object")
    rows.append(("post-edit: bad input never blocks", code == 0 and text == ""))
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
    sys.dont_write_bytecode = True  # no __pycache__ folder in .claude/hooks/
    spec = importlib.util.spec_from_file_location("reviewer_writes", REVIEWER)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    rows.append(("reviewer_writes.py: REVIEWER_AGENTS is the five reviewer agents",
                 set(getattr(mod, "REVIEWER_AGENTS", ())) == set(REVIEWER_AGENTS)))
    for name in REVIEWER_AGENTS:
        agent_file = claude / "agents" / f"{name}.md"
        text = agent_file.read_text() if agent_file.is_file() else ""
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
        for tool, ti, want in (("Bash", {"command": "touch src/main/java/X.java"}, DENY),
                               ("Write", {"file_path": str(repo / "src/main/java/X.java"),
                                          "content": "x"}, DENY)):
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
        for label, ok in post_edit_rows(repo):
            report(ok, label)
    for label, ok in settings_rows():
        report(ok, label)
    print(f"\n{total - failed} of {total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
