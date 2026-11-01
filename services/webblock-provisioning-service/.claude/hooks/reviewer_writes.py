#!/usr/bin/env python3
"""Write limit for the reviewer agents (PreToolUse hook).

Applies to provisioning-validator, final-reviewer, security-reviewer, db-notion-auditor and qa-reviewer
(REVIEWER_AGENTS). It runs from two places:
- the frontmatter of each reviewer agent, with no argument: the limit always applies;
- settings.json, with --if-reviewer-agent: the limit applies only when the input field agent_type names
  a reviewer agent; for any other agent, and for the main session, it prints nothing. Settings hooks
  also run in a `claude -p` session in a folder that was never trusted, where Claude Code skips the
  frontmatter hooks of project agents, so this second hook keeps the limit there.

A reviewer writes only its report under ai/reviews/. This hook denies Write, Edit, MultiEdit and NotebookEdit
anywhere else, and Bash or Monitor commands that write files elsewhere or change git state (git commit,
add, checkout, stash, ...; rm, mv, cp, sed -i, output redirects, ruff format, uv lock, make *-sync).
Read-only commands (tests, grep, git log, diff, show, make qa-sweep) stay allowed; the session guard
(guard.py) still runs on every call.
Python standard library only. Run as: python3 -I .claude/hooks/reviewer_writes.py

It reuses the shell tokenizer of guard.py (loaded by its file path). Like guard.py it is a guard
rail, not a sandbox: a script file or a `python -c` line can still write.
Fail closed: any error or timeout denies the call.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import signal
import sys
from pathlib import Path

BUDGET_SECONDS = 10
REVIEWER_AGENTS = {"provisioning-validator", "final-reviewer", "security-reviewer", "db-notion-auditor",
                   "qa-reviewer"}
SETTINGS_FLAG = "--if-reviewer-agent"
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "Monitor"}
REVIEWS = Path("ai") / "reviews"
DEV_FILES = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"}

ALWAYS_WRITE = {"rm", "rmdir", "mv", "ln", "truncate", "dd", "install", "shred", "unlink", "chmod",
                "chown", "chgrp", "patch", "rsync", "wget", "mkfifo", "mknod", "git-lfs",
                "pre-commit", "goose", "alembic", "kubectl", "oc", "k", "kind", "doctl", "terraform",
                "tofu", "sudo", "doas"}
REVIEW_ONLY = {"touch", "mkdir", "tee"}  # allowed when every path is under ai/reviews/
MAKE_OK = re.compile(r"^(py-[a-z-]+|contracts-check|chart-check|image-scan|qa-sweep|help)$")
GIT_READ = {"log", "diff", "show", "status", "blame", "annotate", "grep", "ls-files", "ls-tree",
            "rev-parse", "rev-list", "describe", "shortlog", "cat-file", "merge-base", "name-rev",
            "show-ref", "for-each-ref", "cherry", "range-diff", "whatchanged", "count-objects",
            "check-ignore", "check-attr", "version", "help", "var", "verify-commit", "show-branch",
            "diff-tree", "diff-index", "diff-files"}
GIT_LISTING = {"-l", "--list", "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose",
               "--show-current", "--contains", "--no-contains", "--merged", "--no-merged",
               "--points-at", "--sort", "--format", "--color", "--no-color", "--column",
               "--no-column", "-i", "--ignore-case"}
GIT_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}


def load_guard():
    spec = importlib.util.spec_from_file_location("guard", Path(__file__).with_name("guard.py"))
    if spec is None or spec.loader is None:
        raise ImportError("guard.py not found")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = None  # guard.py, loaded in main() so a load error is caught and denies


class Denied(Exception):
    pass


def review_roots(cwd: Path) -> list[Path]:
    roots = []
    for base in (os.environ.get("CLAUDE_PROJECT_DIR"), G.run_git(cwd, "rev-parse",
                                                                  "--show-toplevel").strip()):
        if base:
            roots.append(Path(os.path.realpath(Path(base) / REVIEWS)))
    return roots


def in_reviews(raw: str, cwd: Path) -> bool:
    if not raw or G.has_var(raw):
        return False
    p = Path(os.path.realpath(G.resolve(raw, cwd)))
    return any(p == r or r in p.parents for r in review_roots(cwd))


class Shell:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self.cwd_known = True

    def where(self) -> Path:
        if not self.cwd_known:
            raise Denied("the command changes to a folder the hook cannot know, so it cannot check "
                         "where files are written")
        return self.cwd

    def run(self, command: str, depth: int = 0) -> None:
        if depth > 4:
            raise Denied("the command nests shells too deeply to check")
        try:
            toks, subs, bodies = G.tokenize(command)
        except G.ParseError as e:
            raise Denied(f"the command could not be parsed ({e})") from e
        for s in subs:
            self.run(s, depth + 1)
        for seg in G.segments(toks, bodies):
            self.segment(seg, depth)

    def segment(self, seg, depth: int) -> None:
        for target, is_output in seg.redirects:
            if is_output and target not in DEV_FILES and not in_reviews(target, self.where()):
                raise Denied(f"the output redirect writes {target}")
        marks: set[str] = set()
        words = G.strip_wrappers(list(seg.words), {}, marks)
        if not words:
            return
        name = os.path.basename(words[0])
        args = words[1:]
        if "watch" in marks and len(words) == 1 and " " in words[0]:
            self.run(words[0], depth + 1)
            return
        if name in {"cd", "pushd"}:
            target = next((a for a in args if not a.startswith("-") or a == "-"), str(G.HOME))
            target = G.expand(target)
            if target == "-" or G.has_var(target):
                self.cwd_known = False
            else:
                self.cwd = G.resolve(target, self.cwd)
            return
        if name == "popd":
            self.cwd_known = False
            return
        if name in G.SHELLS:
            script = next((args[k + 1] for k, a in enumerate(args)
                           if a.startswith("-") and not a.startswith("--") and "c" in a[1:]
                           and k + 1 < len(args)), None)
            if script is None:
                script = seg.heredoc
            if script is None:
                if seg.piped_in or not [a for a in args if not a.startswith("-")]:
                    raise Denied("a script piped into a shell cannot be checked")
                raise Denied(f"{name} runs a script file the hook cannot check")
            self.run(script, depth + 1)
            return
        if name == "eval":
            self.run(" ".join(args), depth + 1)
            return
        if name in ALWAYS_WRITE:
            raise Denied(f"{name} changes files, git state or a cluster")
        if name.startswith("git-") and len(name) > 4:
            self.git([name[4:], *args])
            return
        if "xargs" in marks and name in G.WRITERS:
            raise Denied(f"xargs {name} changes files the hook cannot see")
        if name in REVIEW_ONLY:
            for p in (a for a in args if not a.startswith("-")):
                if not in_reviews(p, self.where()):
                    raise Denied(f"{name} writes {p}")
            return
        if name == "cp":
            paths = [a for a in args if not a.startswith("-")]
            if not paths or not in_reviews(paths[-1], self.where()):
                raise Denied("cp writes outside ai/reviews/")
            return
        if name == "sed" and any(a.startswith(("-i", "--in-place")) or (
                re.match(r"^-[a-zA-Z]+$", a) and "i" in a) for a in args):
            raise Denied("sed -i edits files in place")
        if name == "perl" and any(re.match(r"^-[a-zA-Z]*i", a) and not a.startswith(("-I", "-M"))
                                  for a in args):
            raise Denied("perl -i edits files in place")
        if name in {"awk", "gawk"} and "inplace" in " ".join(args):
            raise Denied("awk -i inplace edits files in place")
        if name == "find" and any(a in {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint",
                                        "-fprint0", "-fprintf", "-fls"} for a in args):
            raise Denied("find -delete or -exec can change files")
        if name == "curl" and any(a in {"-o", "-O", "--output", "--remote-name", "-T",
                                        "--upload-file"} or a.startswith(("--output=", "-o"))
                                  for a in args):
            raise Denied("curl -o writes a file")
        if name == "tar" and args and re.match(r"^-?[a-zA-Z]*[xcru]", args[0]):
            raise Denied("tar creates or extracts files")
        if name == "ruff":
            if args[:1] == ["format"] and not {"--check", "--diff"} & set(args):
                raise Denied("ruff format rewrites files; use ruff format --check")
            if any(a in {"--fix", "--unsafe-fixes"} or a.startswith("--fix=") for a in args) \
                    and "--no-fix" not in args:
                raise Denied("ruff --fix rewrites files")
        uv_read = args[:1] in (["run"], ["tree"], ["--version"], ["version"], ["help"]) or (
            args[:1] == ["pip"] and args[1:2] in (["list"], ["show"], ["freeze"]))
        if name == "uv" and args and not uv_read:
            raise Denied(f"uv {args[0]} changes the environment or the lock file")
        if name == "uv" and args[:1] == ["run"]:
            inner = G.Segment()
            inner.words = list(args[1:])
            while inner.words and inner.words[0].startswith("-"):
                inner.words.pop(0)
            self.segment(inner, depth + 1)
            return
        if name in G.INSTALLERS or re.match(r"^python[0-9.]*$", name) and args[:2] == ["-m",
                                                                                        "pip"]:
            verbs = {"install", "uninstall", "add", "remove", "update", "upgrade", "i", "sync",
                     "publish", "link", "ci", "lock"}
            if verbs & set(args[:3]):
                raise Denied(f"{name} install changes the environment")
        if name == "make":
            targets = [a for a in args if not a.startswith("-") and "=" not in a]
            bad = [t for t in targets if not MAKE_OK.match(t)]
            if bad or "-C" in args:
                raise Denied(f"make {' '.join(bad) or '-C'} is not a check target (allowed: py-*, "
                             "contracts-check, chart-check, image-scan, qa-sweep)")
        if name in {"docker", "podman"} and args[:1] not in (["ps"], ["images"], ["inspect"],
                                                              ["logs"], ["version"], ["info"]):
            raise Denied(f"{name} {' '.join(args[:1])} changes containers or images")
        if name == "helm" and args[:1] not in (["template"], ["lint"], ["show"], ["version"]):
            raise Denied("helm commands other than template, lint and show change a cluster")
        if name == "git":
            self.git(args)

    def git(self, args: list[str]) -> None:
        i = 0
        cwd = self.where()
        while i < len(args) and args[i].startswith("-"):
            if args[i] in GIT_VALUE:
                if args[i] == "-C":
                    cwd = G.resolve(args[i + 1] if i + 1 < len(args) else ".", cwd)
                if args[i] in {"-c", "--config-env"} and i + 1 < len(args) and \
                        G.GIT_RISKY_CONFIG.match(args[i + 1]):
                    raise Denied("git -c with a key that runs a program")
                i += 2
                continue
            i += 1
        if i >= len(args):
            return
        sub, rest = args[i], args[i + 1:]
        flags = {a.split("=", 1)[0] for a in rest if a.startswith("-")}
        pos = [a for a in rest if not a.startswith("-")]
        if sub in GIT_READ:
            if "--output" in flags:
                raise Denied(f"git {sub} --output writes a file")
            return
        if sub in {"branch", "tag"} and (not pos or flags & {"-l", "--list", "--contains",
                                                              "--merged", "--no-merged",
                                                              "--points-at"}) \
                and flags <= GIT_LISTING:
            return
        if sub == "stash" and pos[:1] in (["list"], ["show"]):
            return
        if sub == "reflog" and pos[:1] in ([], ["show"]):
            return
        if sub == "remote" and pos[:1] in ([], ["show"], ["get-url"]) and len(pos) <= 2:
            return
        if sub == "config" and flags & {"--get", "--list", "-l", "--get-all", "--get-regexp",
                                        "--show-origin"}:
            return
        if sub in {"notes", "submodule"} and pos[:1] in (["list"], ["show"], ["status"]):
            return
        if sub == "worktree":
            if pos[:1] == ["list"]:
                return
            if pos[:1] == ["add"] and not flags & {"-b", "-B", "--orphan", "-f", "--force"} \
                    and len(pos) > 1 and not G.has_var(pos[1]):
                root = G.run_git(cwd, "rev-parse", "--show-toplevel").strip()
                target = Path(os.path.realpath(G.resolve(pos[1], cwd)))
                wt = Path(os.path.realpath(Path(root or cwd) / ".claude" / "worktrees"))
                if wt in target.parents:
                    return
        raise Denied(f"git {sub} changes the repository or its state")


def is_reviewer(event: object) -> bool:
    """True when the input names a reviewer agent. A malformed agent_type is an error (deny)."""
    if not isinstance(event, dict):
        raise TypeError("event is not a JSON object")
    agent = event.get("agent_type")
    if agent is None:
        return False
    if not isinstance(agent, str):
        raise TypeError("agent_type is not a string")
    return agent in REVIEWER_AGENTS


def decide(event: object) -> tuple[str, str] | None:
    if not isinstance(event, dict):
        raise TypeError("event is not a JSON object")
    tool = event.get("tool_name")
    ti = event.get("tool_input") or {}
    if not isinstance(tool, str) or not isinstance(ti, dict):
        raise TypeError("tool_name or tool_input is missing")
    cwd = Path(str(event.get("cwd") or os.getcwd()))
    if tool in WRITE_TOOLS:
        raw = str(ti.get("file_path") or ti.get("notebook_path") or "")
        if in_reviews(raw, cwd):
            return None
        return ("deny", (f"{raw or 'this file'} is outside ai/reviews/. A reviewer writes only its "
                         "report under ai/reviews/; the implementer makes changes."))
    if tool in SHELL_TOOLS:
        command = ti.get("command")
        if tool == "Monitor" and command is None:
            return None
        if not isinstance(command, str) or "\0" in command:
            raise TypeError("command is not a plain string")
        try:
            Shell(cwd).run(command)
        except Denied as e:
            return ("deny", (f"Reviewers are read-only: {e}. Write only the report under "
                             "ai/reviews/, and report what should change instead."))
    return None


def _on_alarm(signum: int, frame: object) -> None:
    raise TimeoutError(f"no decision within {BUDGET_SECONDS} s")


def main() -> int:
    global G
    try:
        signal.signal(signal.SIGALRM, _on_alarm)
        signal.alarm(BUDGET_SECONDS)
        args = sys.argv[1:]
        if args not in ([], [SETTINGS_FLAG]):
            raise ValueError(f"unknown arguments {args}")
        event = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
        if args and not is_reviewer(event):
            verdict = None
        else:
            G = load_guard()
            verdict = decide(event)
    except BaseException as e:  # noqa: BLE001 - an error must never let a write through
        verdict = ("deny", (f"Reviewer write check failed ({type(e).__name__}: {str(e)[:200]}), "
                            "so the call is blocked."))
    finally:
        signal.alarm(0)
    if verdict:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": verdict[0],
            "permissionDecisionReason": "Reviewer limit: " + verdict[1],
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
