#!/usr/bin/env python3
"""Guard rail for Claude Code in the Project service repository (PreToolUse hook).

Reads the hook event (JSON) on stdin and prints a decision on stdout:
deny (blocked, with the reason), ask (the owner confirms), or nothing (normal permission flow).
Python standard library only. Run as: python3 -I .claude/hooks/guard.py

This is a guard rail, not a sandbox: it reads the command text, so a determined workaround
(a script file, a Gradle task, an interpreter one-liner) can get past it. It unwraps the wrappers it
knows (env, nohup, time, timeout, setsid, exec, chronic, systemd-run, taskset, xargs, find -exec,
parallel, flock, script -c, strace, unshare, nsenter, chroot, busybox, coproc, shell keywords and
groups). Any other command whose later words name a cluster tool asks (read-only commands such as
echo, grep and cat are exempt). A command name it cannot read (a variable, $'...' quoting, an alias
or a function) only asks when the command also names a cluster tool or a protected path. A name
hidden from the command text entirely (an alias, function or variable from an earlier command) is
not seen; the shell startup files that could define one (~/.bashrc, ~/.zshrc, ~/.profile, ...) and
BASH_ENV/ENV ask before any change. A container mount whose host side is a variable asks, except
$PWD and $(pwd), which are read as the working folder. A heredoc is read as bash reads it (the
delimiter word with quote removal; no heredoc inside $((...)), ((...)), ${...} or $[...]), and a
body that runs to the end of the command without its end line is denied.
It never runs the command text it checks. The only programs it starts are `git` (fixed argument
lists, no shell, fsmonitor and hooks switched off) to look up the branch, untracked files, aliases,
and for a commit the staged files and a reused message. A commit that changes src/main/, src/test/
(Flyway migrations included), build.gradle or gradle/ with no FR-n or TR-n ID in its message asks the
owner (.claude/rules/spec-driven.md). An edit or shell write of .claude/docs/CONSTITUTION.md asks the
owner, who alone amends it.

Fail closed: a guard error or a check that runs out of time denies Bash, Monitor, an IDE terminal
and MCP writes, and asks for file tools and MCP reads (so a guard bug never silently allows a command,
and never locks out reading). settings.json also sets onFailure "block" for the case where Python
itself cannot start.
"""

from __future__ import annotations

import glob
import csv
import itertools
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

# ----------------------------------------------------------------------------------------------
# Settings (owner rules). Change here, then run test_guard.py and test_hooks.py.
# ----------------------------------------------------------------------------------------------

# Cluster reads (kubectl get/describe/logs, helm list/status) against anything that is not the
# local Kind cluster: "allow", "ask" (owner confirms) or "deny". Reads of the real cluster (DOKS)
# are allowed. Every change to it is denied and is a command written for the owner to run. A Secret
# value is never printed, on any cluster. The local Kind stack is Provisioning's (make proto-* in
# provision-service); its kubeconfig lives under provision-service/bin/kind-state/.
DOKS_READ = "allow"

PROTECTED_BRANCHES = {"main", "master"}

# Untracked owner files (or folders, ending in /) that must never be staged, moved or deleted.
OWNER_UNTRACKED = ("docs/notes/admin-dashboard-monitoring.md",)

# Time budget for one decision (seconds). Must stay below the hook "timeout" in settings.json (15).
BUDGET_SECONDS = 10
# A command longer than this is not parsed; the owner confirms it.
MAX_COMMAND = 300_000
# Glob matches checked per word (more than this: ask).
MAX_GLOB = 200

HOME = Path.home()
WEB_BLOCK = HOME / "Documents" / "web-block"
SECRET_DIRS = [
    WEB_BLOCK / "cred",
    WEB_BLOCK / "secure-key",
    HOME / ".kube",
    HOME / ".ssh",
    HOME / ".gnupg",
    HOME / ".config" / "doctl",
    HOME / ".config" / "gh",
    HOME / ".config" / "glab-cli",
    HOME / ".cloudflared",
    HOME / ".docker",
]
SECRET_FILES = [HOME / ".netrc", HOME / ".pgpass", HOME / ".claude" / ".credentials.json",
                HOME / ".git-credentials"]
SECRET_NAME_RE = re.compile(
    r"^(\.env(\.(?!example$)[^/]+)?|\.envrc|kubeconfig|.+\.kubeconfig|.+-kubeconfig\.ya?ml"
    r"|.+\.(pem|key|p12|pfx|jks)|id_(rsa|ed25519|ecdsa|dsa)|.*dev-tokens.*\.json)$"
)
SECRET_CONTENT_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|client-key-data:\s*\S{20}|\btoken:\s*ey[A-Za-z0-9_-]{20}"
)

# Notion: the database design is read-only (platform-db-v6 hub, its children, and its parent v2),
# except Project's own pages "2 · Project" and "6 · Shared catalog", which the lead edits on the owner's word.
NOTION_PROTECTED = {
    "f8558175966e8233b37e81847a58e2ad": "platform-db-v6 (hub)",
    "40058175966e81f790decccc6e9abf4a": "Database Design",
    "40058175966e812eb644d7bc216fe058": "1 · Account",
    "40058175966e812da0daf97dda9825ef": "3 · Payment",
    "40058175966e81cca2a1fa46a279ad9b": "4 · AI",
    "40058175966e81769596d1ebcadb333d": "5 · Provisioning",
    "40058175966e81c2bf6be0423749ebe7": "7 · Notification",
    "3fd58175966e802e83ccce0cd991fc51": "platform-db-v2 (parent of the hub)",
}
NOTION_EDITABLE = {
    "40358175966e8198a088f123a9ab23fe": "Web Block architecture",
    "3fb58175966e80fba666daa7131deb3e": "Authentication Process Webblock",
    "40058175966e81d4a77bfb48e736757a": "2 · Project",
    "40058175966e81ba92e8e5f6b3e4db2a": "6 · Shared catalog",
}
NOTION_READ_PREFIXES = (
    "notion-search", "notion-fetch", "notion-get-", "notion-list-", "notion-query-",
    "notion-download-", "notion-read-", "notion-memory-search", "notion-show-",
)
# Keys whose values are page text, not the page being changed. Every other key of a Notion write
# (page_id, page_url, parent_page_id, new_parent, view_id, ... and any key added later) is a target.
NOTION_CONTENT_KEYS = {
    "content", "content_updates", "new_str", "old_str", "pages", "properties", "markdown", "text",
    "rich_text", "title", "name", "description", "configure", "statements", "schema", "query",
    "icon", "cover", "filename", "content_type", "page_layout", "instructions_version", "command",
    "type", "position", "message", "prompt", "emoji",
}

# ----------------------------------------------------------------------------------------------
# Decisions
# ----------------------------------------------------------------------------------------------

RANK = {None: 0, "ask": 1, "deny": 2}


class Verdict:
    def __init__(self, decision: str | None = None, reason: str = "") -> None:
        self.decision = decision
        self.reason = reason


OK = Verdict()


def deny(reason: str) -> Verdict:
    return Verdict("deny", reason)


def ask(reason: str) -> Verdict:
    return Verdict("ask", reason)


def worst(verdicts: list[Verdict]) -> Verdict:
    best = OK
    for v in verdicts:
        if RANK[v.decision] > RANK[best.decision]:
            best = v
    return best


class GuardTimeout(Exception):
    pass


# ----------------------------------------------------------------------------------------------
# Shell tokenizer: words, operators, $(...) and `...` bodies, heredoc bodies
# ----------------------------------------------------------------------------------------------

class ParseError(Exception):
    pass


OPS = ["&&", "||", ";;", "|&", ">>", "<<<", "<<", ">|", "&>", ">&", "<&", ";", "&", "|", "(", ")",
       ">", "<", "\n"]
SEPARATORS = {"&&", "||", ";;", "|&", ";", "&", "|", "(", ")", "\n"}
REDIRECTS = {">>", ">|", "&>", ">&", "<&", ">", "<", "<<<"}
OUTPUT_REDIRECTS = {">>", ">|", "&>", ">&", ">"}
HEREDOC_END = set(" \t\n;&|<>()")  # a heredoc delimiter word ends at a blank or a metacharacter


def _heredoc_word(s: str, i: int) -> tuple[str, bool, int]:
    """s[i] follows '<<'. Read the delimiter as bash does: an optional '-', blanks, then one shell
    word with quote removal (E"O"F is EOF, \\EOF is EOF, E@F is E@F). Return (delimiter, quoted,
    index after the word)."""
    n = len(s)
    if i < n and s[i] == "-":
        i += 1
    while i < n and s[i] in " \t":
        i += 1
    out: list[str] = []
    quoted = False
    while i < n and s[i] not in HEREDOC_END:
        c = s[i]
        if c == "\\":
            quoted = True
            out.append(s[i + 1] if i + 1 < n else "")
            i += 2
        elif c in "'\"":
            j = s.find(c, i + 1)
            if j < 0:
                raise ParseError(f"unclosed {c} in a heredoc delimiter")
            part = s[i + 1:j]
            out.append(re.sub(r'\\([\\"$`])', r"\1", part) if c == '"' else part)
            quoted = True
            i = j + 1
        else:
            out.append(c)
            i += 1
    if not out and not quoted:
        raise ParseError("heredoc without a delimiter")
    return "".join(out), quoted, i


def _quoted_end(s: str, j: int) -> int:
    """s[j] is a quote. Index of its closing quote (a double quote honours backslashes)."""
    q, n = s[j], len(s)
    j += 1
    while j < n and s[j] != q:
        j += 2 if q == '"' and s[j] == "\\" else 1
    if j >= n:
        raise ParseError(f"unclosed {q}")
    return j


def _arith_end(s: str, i: int) -> int:
    """s[i:] starts with '((' (of '$((' or an arithmetic command). Return the index after the
    closing '))', or -1 when the parens do not close with '))': bash then reads them as nested
    subshells or $( ( ... ) ). Inside, '<<' is a shift, never a heredoc."""
    depth, j, n, one_at = 0, i, len(s), -2
    while j < n:
        c = s[j]
        if c in "'\"":
            j = _quoted_end(s, j)
        elif c == "\\":
            j += 1
        elif s.startswith("${", j):
            j = _param_end(s, j + 1)
        elif s.startswith("$(", j) and not s.startswith("$((", j):
            j = _balanced(s, j + 1)
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 1:
                one_at = j
            elif depth == 0:
                return j + 1 if one_at == j - 1 else -1
        j += 1
    raise ParseError("unclosed ((")


def _param_end(s: str, i: int) -> int:
    """s[i] is the '{' of '${' (or the '[' of '$['). Return the index of the matching close."""
    open_c, close_c = ("{", "}") if s[i] == "{" else ("[", "]")
    depth, j, n = 0, i, len(s)
    while j < n:
        c = s[j]
        if c in "'\"":
            j = _quoted_end(s, j)
        elif c == "\\":
            j += 1
        elif s.startswith("$((", j):
            k = _arith_end(s, j + 1)
            j = k - 1 if k > 0 else _balanced(s, j + 1)
        elif s.startswith("$(", j):
            j = _balanced(s, j + 1)
        elif c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return j
        j += 1
    raise ParseError(f"unclosed ${open_c}")


def _skip_heredoc_bodies(s: str, i: int, delims: list[str]) -> tuple[int, list[str]]:
    """s[i] starts the line after a heredoc operator. Return the index after the bodies and the bodies.
    A body ends at the first line equal to its delimiter after stripping blanks (bash needs an exact
    line, so the guard never reads a body as longer than bash does)."""
    bodies = []
    n = len(s)
    for delim in delims:
        lines = []
        ended = False
        while i < n:
            j = s.find("\n", i)
            line = s[i:] if j < 0 else s[i:j]
            i = n if j < 0 else j + 1
            if line.strip() == delim:
                ended = True
                break
            lines.append(line)
        if not ended:
            # bash runs such a body as data to the end; the guard would see no command after it.
            raise ParseError(f"heredoc body without its end line {delim!r}")
        bodies.append("\n".join(lines))
    return i, bodies


def _balanced(s: str, i: int) -> int:
    """s[i] is the '(' of '$('. Return the index of the matching ')'. Knows quotes, comments and
    heredocs, so an apostrophe in a commit message heredoc does not break the count."""
    depth, j, quote, n = 0, i, "", len(s)
    pending: list[str] = []
    while j < n:
        c = s[j]
        if quote:
            if c == quote:
                quote = ""
            elif c == "\\" and quote == '"':
                j += 1
        elif c in "'\"":
            quote = c
        elif c == "\\":
            j += 1
        elif c == "#" and j > i and s[j - 1] in " \t\n;(|&":
            while j < n and s[j] != "\n":
                j += 1
            continue
        elif (s.startswith("$((", j) or s.startswith("((", j)) and j > i and (
                k := _arith_end(s, j + 1 if c == "$" else j)) > 0:
            j = k
            continue
        elif s.startswith("${", j) or s.startswith("$[", j):
            j = _param_end(s, j + 1) + 1
            continue
        elif s.startswith("<<", j) and not s.startswith("<<<", j):
            delim, _, j = _heredoc_word(s, j + 2)
            pending.append(delim)
            continue
        elif c == "\n" and pending:
            j, _ = _skip_heredoc_bodies(s, j + 1, pending)
            pending = []
            continue
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    raise ParseError("unbalanced $(")


def _expansions(text: str) -> list[str]:
    """Command substitutions inside text that the shell expands (unquoted heredoc bodies)."""
    subs, i, n = [], 0, len(text)
    while i < n:
        if text[i] == "\\":
            i += 2
            continue
        if text.startswith("$(", i) and not text.startswith("$((", i):
            k = _balanced(text, i + 1)
            subs.append(text[i + 2:k])
            i = k + 1
            continue
        if text[i] == "`":
            k = text.find("`", i + 1)
            if k < 0:
                raise ParseError("unclosed `")
            subs.append(text[i + 1:k])
            i = k + 1
            continue
        i += 1
    return subs


def _sub_marker(inner: str, marker: str) -> str:
    """The word text for a command substitution: $(pwd) is $PWD, anything else is unknown."""
    return "$PWD" if inner.strip() == "pwd" else marker


def tokenize(s: str) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    toks: list[tuple[str, str]] = []
    subs: list[str] = []
    bodies: list[str] = []
    pending: list[tuple[str, bool]] = []
    cur: list[str] | None = None
    i, n = 0, len(s)

    def flush() -> None:
        nonlocal cur
        if cur is not None:
            toks.append(("word", "".join(cur)))
            cur = None

    while i < n:
        c = s[i]
        if c in " \t":
            flush()
            i += 1
            continue
        if c == "#" and cur is None:
            while i < n and s[i] != "\n":
                i += 1
            continue
        if cur is None:
            cur = []
        if c == "\\":
            if i + 1 < n and s[i + 1] == "\n":
                i += 2
                if not cur:
                    cur = None
                continue
            cur.append(s[i + 1] if i + 1 < n else "")
            i += 2
            continue
        if c == "'":
            j = s.find("'", i + 1)
            if j < 0:
                raise ParseError("unclosed '")
            cur.append(s[i + 1:j])
            i = j + 1
            continue
        if c == '"':
            j, part = i + 1, []
            while j < n and s[j] != '"':
                if s[j] == "\\" and j + 1 < n:
                    part.append(s[j + 1])
                    j += 2
                    continue
                if s.startswith("$(", j) and not s.startswith("$((", j):
                    k = _balanced(s, j + 1)
                    subs.append(s[j + 2:k])
                    part.append(_sub_marker(s[j + 2:k], "$(...)"))
                    j = k + 1
                    continue
                if s[j] == "`":
                    k = s.find("`", j + 1)
                    if k < 0:
                        raise ParseError("unclosed `")
                    subs.append(s[j + 1:k])
                    part.append(_sub_marker(s[j + 1:k], "`...`"))
                    j = k + 1
                    continue
                part.append(s[j])
                j += 1
            if j >= n:
                raise ParseError('unclosed "')
            cur.append("".join(part))
            i = j + 1
            continue
        if s.startswith("$((", i) and (k := _arith_end(s, i + 1)) > 0:
            subs.extend(_expansions(s[i + 3:k - 2]))
            cur.append(s[i:k])
            i = k
            continue
        if s.startswith("${", i) or s.startswith("$[", i):
            k = _param_end(s, i + 1)
            subs.extend(_expansions(s[i + 2:k]))
            cur.append(s[i:k + 1])
            i = k + 1
            continue
        if s.startswith("((", i) and not cur and (k := _arith_end(s, i)) > 0:
            subs.extend(_expansions(s[i + 2:k - 2]))
            cur = None
            toks.append(("word", "((...))"))
            i = k
            continue
        if s.startswith("$(", i):
            k = _balanced(s, i + 1)
            subs.append(s[i + 2:k])
            cur.append(_sub_marker(s[i + 2:k], "$(...)"))
            i = k + 1
            continue
        if c == "`":
            k = s.find("`", i + 1)
            if k < 0:
                raise ParseError("unclosed `")
            subs.append(s[i + 1:k])
            cur.append(_sub_marker(s[i + 1:k], "`...`"))
            i = k + 1
            continue
        op = next((o for o in OPS if s.startswith(o, i)), None)
        if op is None:
            cur.append(c)
            i += 1
            continue
        if not cur:
            cur = None
        flush()
        i += len(op)
        if op == "<<":
            delim, quoted, i = _heredoc_word(s, i)
            pending.append((delim, quoted))
            toks.append(("heredoc", str(len(bodies) + len(pending) - 1)))
            continue
        toks.append(("op", op))
        if op == "\n" and pending:
            i, new = _skip_heredoc_bodies(s, i, [d for d, _ in pending])
            for body, (_, quoted) in zip(new, pending):
                if not quoted:
                    subs.extend(_expansions(body))
            bodies.extend(new)
            pending = []
    if cur is not None and not cur:
        cur = None
    flush()
    if pending:
        raise ParseError("heredoc with no body and no end line")
    return toks, subs, bodies


class Segment:
    def __init__(self, piped_in: bool = False) -> None:
        self.words: list[str] = []
        self.redirects: list[tuple[str, bool]] = []  # (target, is_output)
        self.heredoc: str | None = None
        self.piped_in = piped_in


def segments(toks: list[tuple[str, str]], bodies: list[str]) -> list[Segment]:
    out, seg = [], Segment()
    it = iter(range(len(toks)))
    for idx in it:
        kind, val = toks[idx]
        if kind == "op" and val in SEPARATORS:
            if seg.words or seg.redirects or seg.heredoc is not None:
                out.append(seg)
            seg = Segment(piped_in=val in {"|", "|&"})
        elif kind == "op" and val in REDIRECTS:
            if seg.words and seg.words[-1].isdigit():
                seg.words.pop()
            if idx + 1 < len(toks) and toks[idx + 1][0] == "word":
                target = toks[idx + 1][1]
                if val == "<<<":
                    seg.heredoc = target
                elif not target.isdigit() and target != "-":
                    seg.redirects.append((target, val in OUTPUT_REDIRECTS))
                next(it, None)
        elif kind == "heredoc":
            seg.heredoc = bodies[int(val)] if int(val) < len(bodies) else ""
        elif kind == "word":
            seg.words.append(val)
    if seg.words or seg.redirects or seg.heredoc is not None:
        out.append(seg)
    return out


# ----------------------------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------------------------

GIT_SAFE_ENV = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}


def git_out(cwd: Path, *args: str) -> str | None:
    """Read-only git lookups. Never through a shell; fsmonitor and hooks off, so a repository config
    written by an agent cannot make the guard run a program. None when git fails."""
    try:
        r = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
                            "-C", str(cwd), *args], capture_output=True, text=True, timeout=3,
                           check=False, env=GIT_SAFE_ENV, stdin=subprocess.DEVNULL)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def run_git(cwd: Path, *args: str) -> str:
    return git_out(cwd, *args) or ""


def expand(word: str) -> str:
    """Expand the variables an agent uses for home ($HOME, ${HOME}) and a leading ~."""
    word = word.replace("${HOME}", str(HOME)).replace("$HOME", str(HOME))
    return os.path.expanduser(word) if word.startswith("~") else word


def resolve(word: str, cwd: Path) -> Path:
    p = Path(expand(word))
    if not p.is_absolute():
        p = cwd / p
    return Path(os.path.normpath(p))


def exists(p: Path) -> bool:
    try:
        return p.exists()
    except (OSError, ValueError):
        return False


def has_var(word: str) -> bool:
    return "$" in word or "`" in word


def is_secret_path(p: Path) -> bool:
    s = str(p)
    for d in SECRET_DIRS:
        if s == str(d) or s.startswith(str(d) + "/"):
            return True
    if p in SECRET_FILES:
        return True
    return bool(SECRET_NAME_RE.match(p.name))


def inside(p: Path, root: Path) -> bool:
    return str(p) == str(root) or str(p).startswith(str(root).rstrip("/") + "/")


def owner_hit(rel: str) -> list[str]:
    """Owner files (OWNER_UNTRACKED) that a repository-relative path is, holds, or sits inside."""
    rel = rel.rstrip("/")
    hits = []
    for o in OWNER_UNTRACKED:
        o_ = o.rstrip("/")
        if rel in {"", "."} or rel == o_ or rel.startswith(o_ + "/") or o_.startswith(rel + "/"):
            hits.append(o)
    return hits


def scratch_root() -> Path:
    return Path(f"/tmp/claude-{os.getuid()}")


def expand_glob(word: str, cwd: Path) -> list[Path] | None:
    """Paths a glob word matches (None: too many to check). A word without glob characters, or a
    glob that matches nothing, is returned as the one literal path."""
    p = resolve(word, cwd)
    if not any(ch in word for ch in "*?["):
        return [p]
    try:
        found = list(itertools.islice(glob.iglob(str(p), include_hidden=True), MAX_GLOB + 1))
    except (OSError, ValueError, re.error):
        found = []
    if len(found) > MAX_GLOB:
        return None
    return [Path(os.path.normpath(f)) for f in found] or [p]


class Flags(dict):
    """Flag -> value (the last one given), with every (flag, value) in command order."""

    def __init__(self) -> None:
        super().__init__()
        self.history: list[tuple[str, str]] = []

    def __setitem__(self, key: str, value: str) -> None:
        self.history.append((key, value))
        super().__setitem__(key, value)


def split_flags(words: list[str], value_flags: set[str],
                short_values: str = "") -> tuple[list[str], Flags]:
    """Positionals and flags (flag -> value or '') of a command line (words without argv[0]).

    With short_values (the one-letter flags that take a value), a cluster of short flags is read
    as pflag reads it: -Ao yaml is -A and -o yaml, -Aoyaml and -Ao=json give -o its value."""
    pos: list[str] = []
    flags = Flags()
    i = 0
    while i < len(words):
        w = words[i]
        if w == "--":
            pos.extend(words[i + 1:])
            break
        if w.startswith("--") and "=" in w:
            k, v = w.split("=", 1)
            flags[k] = v
        elif w in value_flags:
            flags[w] = words[i + 1] if i + 1 < len(words) else ""
            i += 1
        elif short_values and len(w) > 2 and w[0] == "-" and w[1] != "-":
            for k, ch in enumerate(w[1:], 1):
                if ch in short_values:
                    rest = w[k + 1:]
                    if rest:
                        flags["-" + ch] = rest[1:] if rest[0] == "=" else rest
                    else:
                        flags["-" + ch] = words[i + 1] if i + 1 < len(words) else ""
                        i += 1
                    break
                flags["-" + ch] = ""
        elif w.startswith("-o") and len(w) > 2 and not w.startswith("--"):
            flags["-o"] = w[2:].lstrip("=")
        elif w.startswith("-") and len(w) > 1:
            flags[w] = ""
        else:
            pos.append(w)
        i += 1
    return pos, flags


# ----------------------------------------------------------------------------------------------
# Cluster tools
# ----------------------------------------------------------------------------------------------

LOOPBACK_SERVER = re.compile(r"^https://(127\.0\.0\.1|localhost|\[::1\]):\d+/?$")
KUBECONFIG_SERVER = re.compile(r"""\bserver['"]?\s*:\s*['"]?([^'"\s,}]+)""")
KUBECONFIG_RISKY = re.compile(r"\b(exec|proxy-url|auth-provider)['\"]?\s*:")
# Flags that send a call somewhere else than the kubeconfig's loopback server, or with other
# credentials. With any of them the call does not count as the project's Kind cluster.
KUBECTL_OVERRIDE = {"-s", "--server", "--token", "--username", "--password",
                    "--certificate-authority", "--client-certificate", "--client-key",
                    "--tls-server-name"}
HELM_OVERRIDE = {"--kube-apiserver", "--kube-token", "--kube-ca-file", "--kube-tls-server-name",
                 "--kube-as-user"}


def local_kind(kubeconfig: str | None, context: str | None, cwd: Path) -> bool:
    """True only for a kubeconfig under a bin/kind-state/ folder whose every server is loopback, with
    no exec/proxy/auth-provider entries, and whose context (given or current) starts with kind-."""
    if not kubeconfig or has_var(kubeconfig) or ":" in kubeconfig:
        return False
    p = resolve(kubeconfig, cwd)
    if "/bin/kind-state/" not in str(p):
        return False
    try:
        if not p.is_file() or p.stat().st_size > 1_000_000:
            return False
        text = p.read_text(errors="replace")
    except (OSError, ValueError):
        return False
    servers = KUBECONFIG_SERVER.findall(text)
    if not servers or not all(LOOPBACK_SERVER.match(s) for s in servers):
        return False
    if KUBECONFIG_RISKY.search(text):
        return False
    if context is None:
        m = re.search(r"^current-context:\s*['\"]?([^'\"\s]+)", text, re.M)
        context = m.group(1) if m else ""
    return context.startswith("kind-")


# Flags whose value is a credential. A literal value puts the secret into the command text.
CREDENTIAL_FLAGS = {"--token", "--password", "--kube-token", "--auth-token"}
KUBECONFIG_NAME = re.compile(r"^(kubeconfig|.+\.kubeconfig|.+-kubeconfig\.ya?ml)$")


def credential_flags(name: str, fl: dict[str, str]) -> Verdict:
    for f in sorted(CREDENTIAL_FLAGS & fl.keys()):
        if not fl[f]:
            continue
        if has_var(fl[f]):
            return ask(f"{name} {f} takes a credential from a variable; the guard cannot see where "
                       "it comes from. Use the default kubeconfig identity.")
        return deny(f"{name} {f} writes a credential into the command text. Never write a secret "
                    "value; use the default kubeconfig identity.")
    return OK


def kubeconfig_path(value: str | None, cwd: Path) -> Verdict:
    """--kubeconfig or KUBECONFIG: ~/.kube/config and Provisioning's bin/kind-state/ files are
    fine, a secret file that is not a kubeconfig is denied, any other file asks the owner."""
    if not value:
        return OK
    if has_var(expand(value)):
        return ask(f"The kubeconfig {value} is a variable; the guard cannot see which file it is.")
    verdicts = []
    for part in value.split(":"):
        if not part:
            continue
        p = resolve(part, cwd)
        if p == HOME / ".kube" / "config" or "/bin/kind-state/" in str(p):
            continue
        if is_secret_path(p) and not (inside(p, HOME / ".kube") or KUBECONFIG_NAME.match(p.name)):
            return deny(f"{part} {SECRET_DENY}")
        verdicts.append(ask(f"{part} is neither the default kubeconfig (~/.kube/config) nor "
                            "Provisioning's Kind kubeconfig (bin/kind-state/); the owner confirms."))
    return worst(verdicts)


def verbosity(fl: dict[str, str]) -> int:
    """The klog level of -v/--v/--vmodule; a value the guard cannot read counts as the highest."""
    level = 0
    for k in ("-v", "--v"):
        if k in fl:
            level = max(level, int(fl[k]) if fl[k].isdigit() else 99)
    if "--vmodule" in fl:
        nums = re.findall(r"=(\d+)", fl["--vmodule"])
        level = max(level, *map(int, nums)) if nums else 99
    return level


def output_formats(fl: Flags) -> list[str]:
    """Every -o/--output value in order (pflag uses the last one); [""] when none is given."""
    return [v.lower() for k, v in fl.history if k in {"-o", "--output"}] or [""]


# klog prints request and response bodies from level 6 up (8 and 9: Secret data in full).
MAX_VERBOSITY = 5


# Environment variables that carry a cluster credential.
CREDENTIAL_ENV = re.compile(r"^(HELM_KUBETOKEN|ARGOCD_AUTH_TOKEN|DIGITALOCEAN_ACCESS_TOKEN|"
                            r"(KUBE|K8S|KUBERNETES|HELM|ARGOCD|FLUX|ISTIO|CILIUM|DIGITALOCEAN|DO)"
                            r"_\w*(TOKEN|PASSWORD|PASSWD|SECRET))$")


# Any variable named like a credential, when the command runs a cluster tool.
CREDENTIAL_NAME = re.compile(r"(TOKEN|PASSWORD|PASSWD|SECRET)$", re.I)
CLUSTER_IN_TEXT = re.compile(r"(?<![\w./-])(kubectl[\w.-]*|kubecolor|oc|k|helm|kind|istioctl|"
                             r"cilium|flux|argocd|kubectx|kubens|k9s|stern|doctl)(?![\w-])")


def env_rules(assigned: dict[str, str], cwd: Path, cluster: bool = False) -> Verdict:
    """Variables set for a command, or exported: kubeconfig, kuberc, startup scripts, credentials.
    cluster: the command runs a cluster tool, so any credential-named variable counts."""
    verdicts = [kubeconfig_path(assigned.get("KUBECONFIG"), cwd)]
    if assigned.get("KUBERC", "off") != "off":
        verdicts.append(deny("KUBERC loads kubectl default flags and aliases the guard cannot see. "
                             "Use KUBERC=off or leave it out."))
    for k in ("BASH_ENV", "ENV"):
        if k in assigned:
            verdicts.append(ask(f"{k} makes every shell run a script first, which hides commands "
                                "from the guard; the owner approves."))
    for k, v in assigned.items():
        if k == "ARGOCD_OPTS":
            _, fl = split_flags(v.split(), GENERIC_CLUSTER["argocd"][0])
            verdicts.append(credential_flags("ARGOCD_OPTS", fl))
        elif (CREDENTIAL_ENV.match(k) or (cluster and CREDENTIAL_NAME.search(k))) and v:
            verdicts.append(ask(f"{k} takes a credential from a variable; the guard cannot see "
                                "where it comes from.") if has_var(v) else
                            deny(f"{k} writes a credential into the command text. Never write a "
                                 "secret value; use the default kubeconfig identity."))
    return worst(verdicts)


def doks_read(what: str) -> Verdict:
    msg = (f"{what} reads a cluster that is not the local Kind cluster (no --kubeconfig under "
           "bin/kind-state/). DOKS is read-only for Claude (.claude/CLAUDE.md).")
    if DOKS_READ == "allow":
        return OK
    return deny(msg) if DOKS_READ == "deny" else ask(msg)


def cluster_write(what: str) -> Verdict:
    return deny(f"{what} changes a cluster. DOKS is read-only: write the command down for the owner "
                "to run. The local Kind stack is Provisioning's: use its make proto-* targets, or pass "
                "--kubeconfig <provision-service>/bin/kind-state/... and --context kind-....")


KUBECTL_VALUE = {
    "--kubeconfig", "--context", "--cluster", "--user", "-n", "--namespace", "-s", "--server",
    "--token", "--as", "--as-group", "--as-uid", "--cache-dir", "--request-timeout", "-o",
    "--output", "-l", "--selector", "-f", "--filename", "-c", "--container", "--field-selector",
    "--template", "-L", "--sort-by", "--since", "--tail", "--raw", "-k", "--kustomize",
    "--profile", "-v", "--v", "--certificate-authority", "--client-certificate",
    "--client-key", "--tls-server-name", "--log-file", "--username", "--password",
    "--profile-output", "--log-dir", "--vmodule", "--log-file-max-size", "--log-backtrace-at",
    "--chunk-size", "--kuberc",
}
KUBECTL_READ = {"get", "describe", "logs", "top", "version", "api-resources", "api-versions",
                "explain", "cluster-info", "events", "wait", "kustomize", "completion", "help",
                "options"}
# Flags that pick the objects from a file or a kustomization, so the guard cannot see the kinds.
KUBECTL_FILE = {"-f", "--filename", "-k", "--kustomize"}
# One-letter kubectl flags: those that take a value, and the known ones that do not. Any other
# letter in a cluster (-Zo) means the guard is unsure how kubectl reads it.
KUBECTL_SHORT_VALUE = "nolfcLksv"
KUBECTL_SHORT_BOOL = "AwRithpq"
# Output formats that never print Secret values. Everything else (yaml, json, kyaml, jsonpath,
# go-template, custom-columns, and any format added later) is denied for Secrets.
SAFE_SECRET_FORMATS = {"", "name", "wide"}


def check_kubectl(words: list[str], env: dict[str, str], cwd: Path,
                  piped_args: bool = False) -> Verdict:
    """piped_args: xargs, parallel or find -exec add words the guard cannot see."""
    _, fl = split_flags(words[1:], KUBECTL_VALUE, KUBECTL_SHORT_VALUE)
    return worst([credential_flags("kubectl", fl), kubeconfig_path(fl.get("--kubeconfig"), cwd),
                  kubectl_rules(words, env, cwd, piped_args)])


def kubectl_rules(words: list[str], env: dict[str, str], cwd: Path, piped_args: bool) -> Verdict:
    pos, fl = split_flags(words[1:], KUBECTL_VALUE, KUBECTL_SHORT_VALUE)
    if "--kuberc" in fl and fl["--kuberc"] != "off":
        return deny("kubectl --kuberc loads default flags and aliases the guard cannot see (an "
                    "-o yaml for get, say). Use --kuberc=off or leave it out.")
    local = local_kind(fl.get("--kubeconfig") or env.get("KUBECONFIG"), fl.get("--context"), cwd)
    if KUBECTL_OVERRIDE & fl.keys():
        local = False
    verb = pos[0] if pos else ""
    sub = pos[1] if len(pos) > 1 else ""
    formats = output_formats(fl)
    # Secrets are never printed, not even on Kind.
    resources = [r.split("/")[0].split(".")[0].lower() for p in pos[1:] for r in p.split(",")]
    unsure = any(len(k) == 2 and k[1] not in KUBECTL_SHORT_VALUE + KUBECTL_SHORT_BOOL
                 for k in fl)
    # xargs, parallel and find -exec can add -o from outside the command text.
    full = (any(f not in SAFE_SECRET_FORMATS for f in formats) or "--template" in fl or unsure
            or piped_args)
    # The words that name a kind: the first one (TYPE or TYPE/NAME) and every later TYPE/NAME.
    kinds = [r.split("/")[0] for p in pos[1:2] + [w for w in pos[2:] if "/" in w]
             for r in p.split(",")]
    secret = "secret" in resources or "secrets" in resources
    unknown = bool(KUBECTL_FILE & fl.keys()) or any(map(has_var, kinds)) or piped_args
    # A $ or backtick word splits at run time and can add -o yaml the guard never sees.
    if (secret or unknown) and any(has_var(expand(w)) for w in words[1:]):
        full = True
    if verb == "get" and secret and full:
        return deny("Prints Secret values (kubectl get secret -o yaml/json/jsonpath/template/...). "
                    "Use kubectl get secret (no -o) or -o name.")
    if verb == "describe" and (secret or unknown):
        return deny("kubectl describe secret prints service-account token values in clear text "
                    "(and -f/-k/$VAR/xargs may name a Secret). Use kubectl get secret (no -o).")
    if verb == "get" and full and unknown:
        return deny("kubectl get -f/-k/$VAR/xargs with a full output format may print Secret "
                    "values the guard cannot see. Name the resource type, or drop -o.")
    if verbosity(fl) > MAX_VERBOSITY and (not local or secret or unknown or "--raw" in fl):
        return deny(f"kubectl -v above {MAX_VERBOSITY} logs request and response bodies, Secret "
                    f"data included. Use -v={MAX_VERBOSITY} or less.")
    raw = unquote(fl.get("--raw", "")).lower()
    if "/secrets" in raw or has_var(raw):
        return deny("Reads Secret values through --raw (or a --raw path the guard cannot read).")
    if "/proxy" in raw and not local:
        return cluster_write("kubectl get --raw .../proxy (it reaches into pods and services, as "
                             "kubectl proxy does)")
    if verb == "create" and sub == "token":
        return deny("kubectl create token prints a credential.")
    if verb == "config" and sub == "view" and ("--raw" in fl or "--flatten" in fl):
        return deny("kubectl config view --raw/--flatten prints credentials.")
    if verb == "config" and sub == "view" and not local:
        return deny("kubectl config view prints the kubeconfig, and it does not hide every "
                    "credential (password, auth-provider tokens, exec env). Use kubectl config "
                    "get-contexts or current-context.")
    if fl.get("--dry-run") == "server":
        return OK if local else cluster_write("--dry-run=server (it still sends the write)")
    read = (verb in KUBECTL_READ
            or (verb == "auth" and sub in {"can-i", "whoami"})
            or (verb == "rollout" and sub in {"status", "history"})
            or (verb == "config" and sub in {"view", "get-contexts", "current-context",
                                             "get-clusters", "get-users"})
            or (verb == "plugin" and sub == "list"))
    if read:
        if local or verb in {"kustomize", "completion", "help", "options"} or (
                verb == "version" and "--client" in fl):
            return OK
        return doks_read(f"kubectl {verb}")
    if local:
        return OK
    return cluster_write(f"kubectl {verb or '(no verb)'}")


HELM_VALUE = {"--kubeconfig", "--kube-context", "-n", "--namespace", "--kube-apiserver",
              "--kube-token", "--kube-as-user", "--kube-as-group", "--kube-ca-file",
              "--kube-tls-server-name", "--registry-config", "--repository-config",
              "--repository-cache", "-f", "--values", "--set", "--set-string", "--set-file",
              "--set-json", "--version", "-o", "--output", "--revision", "--timeout",
              "--burst-limit", "--qps", "--post-renderer", "--description", "--username",
              "--password", "-v", "--v"}
HELM_SHORT_VALUE = "nofmv"
HELM_OFFLINE = {"template", "lint", "show", "inspect", "version", "search", "env", "help",
                "completion", "verify", "package", "pull", "fetch", "dependency", "dep", "repo",
                "create"}
HELM_CLUSTER_READ = {"list", "ls", "status", "history", "hist"}


def check_helm(words: list[str], env: dict[str, str], cwd: Path,
               piped_args: bool = False) -> Verdict:
    _, fl = split_flags(words[1:], HELM_VALUE, HELM_SHORT_VALUE)
    return worst([credential_flags("helm", fl), kubeconfig_path(fl.get("--kubeconfig"), cwd),
                  helm_rules(words, env, cwd, piped_args)])


def helm_rules(words: list[str], env: dict[str, str], cwd: Path, piped_args: bool) -> Verdict:
    pos, fl = split_flags(words[1:], HELM_VALUE, HELM_SHORT_VALUE)
    local = local_kind(fl.get("--kubeconfig") or env.get("KUBECONFIG"), fl.get("--kube-context"),
                       cwd)
    if HELM_OVERRIDE & fl.keys():
        local = False
    verb = pos[0] if pos else ""
    sub = pos[1] if len(pos) > 1 else ""
    if fl.get("--dry-run") == "server" and not local:
        return cluster_write(f"helm {verb} --dry-run=server (it sends the release or runs its "
                             "lookups against the cluster)")
    if verb == "template" and "--validate" in fl and not local:
        return deny("helm template --validate contacts the cluster, and chart lookups can print "
                    "live Secrets. Render without --validate.")
    if verbosity(fl) > MAX_VERBOSITY and not local:
        return deny(f"helm -v above {MAX_VERBOSITY} logs request and response bodies.")
    if verb in {"status", "get"} and any(has_var(expand(w)) for w in words[1:]):
        return deny(f"helm {verb} with a $ or backtick word: it can add -o json or a get "
                    "subcommand that prints Secret data at run time. Write the words out.")
    if verb in {"status", "get"} and piped_args:
        return deny(f"xargs helm {verb}: the words from the pipe can add -o json or a get "
                    "subcommand that prints Secret data. Write the command out.")
    if verb == "status" and any(f not in {"", "table"} for f in output_formats(fl)):
        return deny("helm status -o json/yaml prints the release values and manifest (Secret "
                    "data). Use helm status, or -o table.")
    if verb in HELM_OFFLINE:
        return OK
    if verb == "plugin":
        return OK if sub == "list" else ask("helm plugin install/update runs third-party code.")
    if verb == "get":
        if sub in {"all", "manifest", "values", "hooks"}:
            return deny(f"helm get {sub} can print Secret data, on any cluster. Use helm "
                        "status/history/list.")
        return OK if local else doks_read(f"helm get {sub}")
    if verb in {"install", "upgrade"} and "--dry-run" in fl and not local:
        return deny(f"helm {verb} --dry-run on the real cluster prints the rendered release, which "
                    "can carry its stored values (Secret data). Render with helm template.")
    if verb in HELM_CLUSTER_READ:
        return OK if local else doks_read(f"helm {verb}")
    if verb == "push" or (verb == "registry" and sub == "login"):
        target = " ".join(pos[1:])
        if verb == "push" and re.search(r"oci://(localhost|127\.0\.0\.1)[:/]", target):
            return OK
        return deny("helm push / registry login publishes or stores credentials; the owner does it.")
    if local:
        return OK
    return cluster_write(f"helm {verb or '(no verb)'}")


def check_kind(words: list[str], env: dict[str, str], cwd: Path) -> Verdict:
    _, fl = split_flags(words[1:], {"--kubeconfig", "--name", "-n", "--config", "--image"})
    return worst([kubeconfig_path(fl.get("--kubeconfig"), cwd), kind_rules(words, env, cwd)])


def kind_rules(words: list[str], env: dict[str, str], cwd: Path) -> Verdict:
    pos, fl = split_flags(words[1:], {"--kubeconfig", "--name", "-n", "--config", "--image"})
    verb = pos[0] if pos else ""
    if verb == "get" and pos[1:2] == ["kubeconfig"]:
        return deny("kind get kubeconfig prints the cluster's admin credentials. Use Provisioning's "
                    "make proto-* targets (they use bin/kind-state/proto/kubeconfig) instead.")
    if verb in {"get", "version", "help", "completion", "build", "load"}:
        return OK
    if "--all" in fl:
        return deny("kind delete clusters --all removes other projects' clusters.")
    kc = fl.get("--kubeconfig") or env.get("KUBECONFIG") or ""
    if not kc or "/bin/kind-state/" not in str(resolve(kc, cwd)):
        return deny(f"kind {verb} without --kubeconfig .../bin/kind-state/... writes ~/.kube/config. "
                    "The Kind stack is Provisioning's: run its make proto-up / proto-down.")
    if verb == "delete":
        return ask(f"kind {verb} changes a local Kind cluster; prefer Provisioning's make proto-*.")
    return OK


GENERIC_CLUSTER = {
    # tool: (value flags, read words)
    "istioctl": ({"--kubeconfig", "--context", "-n", "--namespace", "-i", "--istioNamespace", "-o",
                  "--level"},
                 {"version", "analyze", "proxy-status", "ps", "proxy-config", "pc", "help",
                  "verify-install", "completion"}),
    "cilium": ({"--kubeconfig", "--context", "-n", "--namespace", "-o"},
               {"status", "version", "help", "observe", "list"}),
    "argocd": ({"--server", "--auth-token", "-o", "--grpc-web-root-path", "--username",
                "--password"},
               {"version", "list", "get", "history", "diff", "logs", "resources", "help"}),
    # flux diff is not a read: it sends a server-side dry-run apply, as kubectl --dry-run=server.
    "flux": ({"--kubeconfig", "--context", "-n", "--namespace"},
             {"get", "check", "version", "logs", "events", "tree", "stats", "help"}),
}


def check_generic_cluster(name: str, words: list[str], env: dict[str, str], cwd: Path) -> Verdict:
    _, fl = split_flags(words[1:], GENERIC_CLUSTER[name][0])
    return worst([credential_flags(name, fl), kubeconfig_path(fl.get("--kubeconfig"), cwd),
                  generic_cluster_rules(name, words, env, cwd)])


def generic_cluster_rules(name: str, words: list[str], env: dict[str, str], cwd: Path) -> Verdict:
    vflags, reads = GENERIC_CLUSTER[name]
    pos, fl = split_flags(words[1:], vflags)
    local = local_kind(fl.get("--kubeconfig") or env.get("KUBECONFIG"), fl.get("--context"), cwd)
    if name == "argocd" and pos[:1] in (["login"], ["account"], ["cert"], ["gpg"]):
        return deny(f"argocd {pos[0]} handles credentials; the owner does it.")
    is_read = bool(pos) and (pos[0] in reads or len(pos) > 1 and pos[1] in reads)
    if name == "cilium" and pos[:2] == ["config", "view"]:
        is_read = True
    # These read words change something with these flags: argocd --refresh patches the
    # Application, --server-side-diff sends a server-side apply dry run, istioctl proxy-config log
    # --level/--reset sets the proxies' log levels.
    if name == "argocd" and fl.keys() & {"--refresh", "--hard-refresh", "--server-side-diff"}:
        is_read = False
    if name == "istioctl" and pos[:1] in (["proxy-config"], ["pc"]) and pos[1:2] == ["log"] and (
            fl.keys() & {"--level", "-r", "--reset"}):
        is_read = False
    if is_read:
        return OK if local else doks_read(f"{name} {' '.join(pos[:2])}")
    if local:
        return OK
    return cluster_write(f"{name} {' '.join(pos[:2])}")


def check_doctl(words: list[str]) -> Verdict:
    pos, _ = split_flags(words[1:], {"-t", "--access-token", "--context", "-o", "--output",
                                     "--format", "-c", "--config"})
    if "kubeconfig" in pos or "auth" in pos or "login" in pos:
        return deny("doctl kubeconfig/auth/login stores or prints credentials; the owner does it.")
    if pos and pos[-1] in {"list", "ls", "get", "g", "version"}:
        return OK
    return deny("doctl changes DigitalOcean resources; write the command down for the owner.")


def check_cloudflared(words: list[str]) -> Verdict:
    pos, _ = split_flags(words[1:], {"--config", "--origincert", "--loglevel", "--logfile",
                                     "--cred-file", "--credentials-file"})
    if pos in (["version"], [], ["tunnel", "list"]) or pos[:2] == ["tunnel", "info"]:
        return OK
    return deny("cloudflared changes tunnels or prints tokens; write the command down for the owner.")


def check_gh(name: str, words: list[str]) -> Verdict:
    pos = [w for w in words[1:] if not w.startswith("-")]
    flags = {w.split("=", 1)[0] for w in words[1:] if w.startswith("-")}
    if pos[:2] == ["auth", "token"] or (pos[:2] == ["auth", "status"] and flags & {
            "-t", "--show-token"}) or (pos[:2] == ["config", "get"] and "token" in pos[2:]):
        return deny(f"{name} {' '.join(pos[:2])} prints a credential.")
    if pos[1:2] and pos[1] not in {"view", "list", "status", "diff", "checks"}:
        return ask(f"{name} {' '.join(pos[:2])} writes to GitLab/GitHub.")
    return OK


# ----------------------------------------------------------------------------------------------
# git
# ----------------------------------------------------------------------------------------------

GIT_GLOBAL_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
                    "--super-prefix", "--config-env"}
# Config keys that run a program or rename a command. Passing them with git -c needs the owner.
GIT_RISKY_CONFIG = re.compile(r"^(alias\.|core\.(sshcommand|pager|editor|fsmonitor|hookspath|"
                              r"gitproxy)|credential\.|.*\.helper$|gpg\.program|diff\.external|"
                              r"sequence\.editor|.*\.(command|textconv|clean|smudge|process)$|"
                              r"remote\..*\.(receivepack|uploadpack)|clean\.requireforce)", re.I)
GIT_BUILTINS = {
    "add", "am", "apply", "archive", "bisect", "blame", "branch", "bundle", "checkout",
    "cherry-pick", "clean", "clone", "commit", "config", "describe", "diff", "fetch", "format-patch",
    "gc", "grep", "init", "log", "maintenance", "merge", "mv", "notes", "pull", "push", "range-diff",
    "rebase", "reflog", "remote", "repack", "replace", "reset", "restore", "revert", "rm", "shortlog",
    "show", "sparse-checkout", "stash", "status", "submodule", "switch", "tag", "worktree",
    "cat-file", "check-ignore", "count-objects", "for-each-ref", "ls-files", "ls-remote", "ls-tree",
    "merge-base", "rev-list", "rev-parse", "show-ref", "symbolic-ref", "update-index", "update-ref",
    "verify-commit", "help", "version", "var", "send-email", "send-pack", "http-push",
    "filter-branch", "fsck", "prune", "whatchanged", "cherry", "difftool", "request-pull", "svn",
    "p4", "hash-object", "blame", "annotate", "show-branch", "name-rev", "check-attr",
}


class Ctx:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self.cwds = [cwd]          # every directory a `cd` in this command may have moved to
        self.cwd_unknown = False   # a `cd $X` / `cd -` / `popd` moved to a directory we cannot know
        self.branches: dict[str, str] = {}   # repo root (or folder) -> branch a checkout moved to
        self.worktrees: dict[str, str] = {}  # folder a worktree add makes -> its branch ("" unknown)
        self.worktree_roots: dict[str, Path | None] = {}  # that folder -> the repository it shares
        self._roots: dict[str, Path | None] = {}
        self.names: set[str] = set()
        self.decoder = False
        self.secret_words = False
        self.piped_script = False
        self.raw = ""              # the whole command text, for commit messages built by $(...)
        self.stage_specs: list[tuple[Path, str, list[str]]] = []  # git add/rm/mv before a commit

    def git_root(self, cwd: Path) -> Path | None:
        if str(cwd) not in self._roots:
            top = run_git(cwd, "rev-parse", "--show-toplevel").strip()
            self._roots[str(cwd)] = Path(top) if top else None
        return self._roots[str(cwd)]

    def repo(self) -> Path:
        return self.git_root(self.cwds[0]) or Path(os.environ.get("CLAUDE_PROJECT_DIR",
                                                                  self.cwds[0]))

    def branch_key(self, cwd: Path) -> str:
        return str(self.git_root(cwd) or cwd)

    def worktree_of(self, cwd: Path) -> str | None:
        """The worktree this command added that holds cwd, if any."""
        return next((str(f) for f in (cwd, *cwd.parents) if str(f) in self.worktrees), None)

    def set_branch(self, cwd: Path, name: str) -> None:
        """A checkout or switch at cwd moved to name ("" when the guard cannot name the branch)."""
        wt = self.worktree_of(cwd)
        if wt is not None:
            self.worktrees[wt] = name
        else:
            self.branches[self.branch_key(cwd)] = name

    def is_local_branch(self, cwd: Path, name: str) -> bool:
        wt = self.worktree_of(cwd)
        repo = self.worktree_roots.get(wt) if wt is not None else self.git_root(cwd)
        return bool(repo and run_git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options",
                                     f"refs/heads/{name}").strip())

    def current_branch(self, cwd: Path) -> str:
        """The branch at cwd: one this command made or moved to, else git's answer ("" unknown)."""
        wt = self.worktree_of(cwd)
        if wt is not None:
            return self.worktrees[wt]
        key = self.branch_key(cwd)
        if key in self.branches:
            return self.branches[key]
        return run_git(cwd, "rev-parse", "--abbrev-ref", "HEAD").strip()


def untracked(cwd: Path, pathspec: str) -> list[str]:
    out = run_git(cwd, "ls-files", "--others", "--exclude-standard", "--full-name", "-z", "--",
                  pathspec)
    return [f for f in out.split("\0") if f]


# Options that make a new branch: (options followed by the name, long options with =name).
NEW_BRANCH_FLAGS = {"checkout": ({"-b", "-B", "--orphan"}, set()),
                    "switch": ({"-c", "-C", "--create", "--force-create", "--orphan"},
                               {"--create", "--force-create", "--orphan"}),
                    "worktree": ({"-b", "-B"}, set())}
# The options of those that reset a branch that already exists.
RESET_BRANCH_FLAGS = {"checkout": ({"-B"}, set()),
                      "switch": ({"-C", "--force-create"}, {"--force-create"}),
                      "worktree": ({"-B"}, set())}


def new_branch(args: list[str], spec: tuple[set[str], set[str]]) -> str | None:
    """The branch name a -b/-c style option makes; "" if the name cannot be read; None if none."""
    spaced, with_eq = spec
    for k, a in enumerate(args):
        if a == "--":
            break
        if a in spaced:
            name = args[k + 1] if k + 1 < len(args) else ""
            return "" if has_var(name) or name.startswith("-") else name
        key, eq, val = a.partition("=")
        if eq and key in with_eq:
            return "" if has_var(val) else val
        short = {f[1] for f in spaced if len(f) == 2}
        if not a.startswith("--") and len(a) > 1 and a[0] == "-":
            for j, ch in enumerate(a[1:], start=1):  # -Bmain, -qB main
                if ch in short:
                    name = a[j + 1:] or (args[k + 1] if k + 1 < len(args) else "")
                    return "" if has_var(name) or name.startswith("-") else name
    return None


def resets_protected(sub: str, args: list[str]) -> str | None:
    """The protected branch a -B/-C/--force-create option would reset, if any."""
    name = new_branch(args, RESET_BRANCH_FLAGS[sub])
    return name if name in PROTECTED_BRANCHES else None


def note_worktree_add(args: list[str], cwd: Path, ctx: Ctx) -> None:
    """Remember the branch a new worktree gets: the -b/-B name, else a protected one, else unknown."""
    made = new_branch(args, NEW_BRANCH_FLAGS["worktree"])
    plain, k = [], 0
    while k < len(args):
        a = args[k]
        if a == "--reason" or (not a.startswith("--") and a[:1] == "-" and a[-1:] in "bB"
                               and "b" not in a[1:-1] and "B" not in a[1:-1]):
            k += 2  # the value of -b/-B (also -fB) or --reason
            continue
        if a == "--":
            plain += args[k + 1:]
            break
        if not a.startswith("-"):
            plain.append(a)
        k += 1
    if not plain:
        return
    folder = resolve(plain[0], cwd)
    if exists(folder) and ctx.git_root(folder) == folder:
        return  # the folder is already a worktree (the add fails): git reads its branch
    if made is not None:
        branch = made
    elif len(plain) > 1:
        branch = plain[1] if plain[1] in PROTECTED_BRANCHES else ""
    else:
        branch = folder.name if folder.name in PROTECTED_BRANCHES else ""
    ctx.worktrees[str(folder)] = branch
    ctx.worktree_roots[str(folder)] = ctx.git_root(cwd)


def check_git(words: list[str], ctx: Ctx, depth: int = 0) -> Verdict:
    i, cwd = 1, ctx.cwd
    cwd_known = not ctx.cwd_unknown  # a git -C <absolute path> names the folder again
    other_repo = False               # --git-dir or --work-tree: the guard cannot read the branch
    risky_config = []
    while i < len(words) and words[i].startswith("-"):
        w = words[i]
        if w.split("=", 1)[0] in {"--git-dir", "--work-tree"}:
            other_repo = True
        if w in GIT_GLOBAL_VALUE:
            val = words[i + 1] if i + 1 < len(words) else ""
            if w == "-C":
                cwd = resolve(val, cwd)
                cwd_known = cwd_known or (expand(val).startswith("/") and not has_var(val))
            if w in {"-c", "--config-env"} and GIT_RISKY_CONFIG.match(val):
                risky_config.append(val.split("=", 1)[0])
            i += 2
            continue
        if w.startswith("--config-env=") and GIT_RISKY_CONFIG.match(w.split("=", 1)[1]):
            risky_config.append(w.split("=", 1)[1].split("=", 1)[0])
        i += 1
    if i >= len(words):
        return OK
    sub, args = words[i], words[i + 1:]
    if risky_config:
        return deny(f"git -c {', '.join(risky_config)} renames a command or runs a program, so the "
                    "guard cannot see what git will do. Run the plain git command.")
    flags = {a.split("=", 1)[0] for a in args if a.startswith("-")}
    pos = [a for a in args if not a.startswith("-")]
    short = "".join(a[1:] for a in args if a.startswith("-") and not a.startswith("--"))

    if sub not in GIT_BUILTINS and depth < 3:
        alias = run_git(cwd, "config", "--get", f"alias.{sub}").strip()
        if alias:
            if alias.startswith("!"):
                return analyse(alias[1:] + " " + " ".join(args), ctx, depth + 1)
            return check_git(["git", *words[1:i], *alias.split(), *args], ctx, depth + 1)
    branch = ctx.current_branch(cwd)
    if sub in {"add", "rm", "mv"}:
        ctx.stage_specs.append((cwd, sub, args))

    if sub in {"push", "send-pack", "http-push"} or (sub == "svn" and pos[:1] == ["dcommit"]) or (
            sub == "p4" and pos[:1] == ["submit"]):
        return deny("git push is the owner's job. Commit locally and tell the owner what to push.")
    if sub in {"rebase", "filter-branch", "filter-repo", "replace"}:
        return deny(f"git {sub} rewrites history.")
    if sub == "submodule" and pos[:1] == ["foreach"]:
        rest = [a for a in args[args.index("foreach") + 1:] if a not in {"--recursive", "-q",
                                                                          "--quiet"}]
        return worst([analyse(" ".join(rest), ctx, depth + 1),
                      ask("git submodule foreach runs a command in every submodule.")])
    if sub == "bisect" and pos[:1] == ["run"]:
        return worst([analyse(" ".join(args[args.index("run") + 1:]), ctx, depth + 1),
                      ask("git bisect run runs a command on many commits.")])
    if sub == "send-email":
        return ask("git send-email sends patches out by mail.")
    if sub == "reset":
        if flags & {"--hard", "--merge", "--keep"}:
            return deny("git reset --hard/--merge/--keep throws work away (other agents share this "
                        "tree).")
        before = args[:args.index("--")] if "--" in args else args
        for p in (a for a in before if not a.startswith("-")):
            if p not in {"HEAD", "@"} and not exists(resolve(p, cwd)):
                return ask(f"git reset {p} moves the branch to another commit.")
        return OK
    if sub == "clean":
        if "--dry-run" in flags or "n" in short:
            return OK
        return deny("git clean deletes untracked files; the owner's "
                    f"{', '.join(OWNER_UNTRACKED)} is untracked. Use git clean -n to look.")
    if sub in {"checkout", "switch"}:
        if flags & {"-f", "--force", "--discard-changes"} or (sub == "checkout" and "f" in short):
            return deny(f"git {sub} --force throws away uncommitted work.")
        if sub == "checkout":
            if "--" in args or "." in pos or ":/" in pos:
                target = args[args.index("--") + 1:] if "--" in args else pos
                if "." in target or ":/" in target:
                    return deny("git checkout . discards every uncommitted change in the shared "
                                "tree.")
                return ask("git checkout -- <path> discards uncommitted changes to those files.")
            if not flags & {"-b", "-B", "--orphan"} and (
                    len(pos) > 1 or (pos and exists(resolve(pos[0], cwd)))):
                return ask("git checkout <commit> <path> (or a file name) overwrites uncommitted "
                           "changes to those files.")
        reset = resets_protected(sub, args)
        if reset:
            return deny(f"git {sub} resets {reset}: moving a protected branch needs the owner.")
        made = new_branch(args, NEW_BRANCH_FLAGS[sub])
        if made is not None:
            moved_to: str | None = made
        elif "--detach" in flags or (sub == "switch" and "d" in short) or "-" in args:
            moved_to = ""  # a detached HEAD or the previous branch: the guard cannot name it
        elif pos:
            name = pos[0]
            moved_to = name if name in PROTECTED_BRANCHES or ctx.is_local_branch(cwd, name) else ""
        else:
            moved_to = None
        # A protected branch stays: if this checkout fails (a later `;`), the merge lands on it.
        if moved_to is not None and (moved_to in PROTECTED_BRANCHES
                                     or branch not in PROTECTED_BRANCHES):
            ctx.set_branch(cwd, moved_to)
        if flags & {"-B", "-C", "--force-create"} or new_branch(args, RESET_BRANCH_FLAGS[sub]) \
                is not None:
            return ask(f"git {sub} -B/-C resets an existing branch.")
        return OK
    if sub == "restore":
        if flags & {"--staged", "-S"} and not flags & {"--worktree", "-W"}:
            return OK
        if "." in pos or ":/" in pos:
            return deny("git restore . discards every uncommitted change in the shared tree.")
        return ask("git restore discards uncommitted changes.")
    if sub == "stash":
        if pos[:1] == ["clear"]:
            return deny("git stash clear deletes every stash.")
        if pos[:1] in (["list"], ["show"]):
            return OK
        if flags & {"-u", "--include-untracked", "-a", "--all"} or re.search(r"[ua]", short):
            return deny("git stash -u/-a would move untracked owner files "
                        f"({', '.join(OWNER_UNTRACKED)}) out of the tree.")
        return ask("git stash moves uncommitted work of other agents in the shared tree.")
    if sub == "branch":
        if flags & {"-d", "-D", "--delete", "-f", "--force"} or re.search(r"[dDf]", short):
            return deny("Deleting or force-moving branches needs the owner.")
        if flags & {"-m", "-M", "--move", "-c", "-C", "--copy"} or re.search(r"[mMcC]", short):
            names = set(pos)
            if len(pos) == 1 and (flags & {"-m", "-M", "--move"} or re.search(r"[mM]", short)):
                names.add(branch)  # git branch -m <new> renames the current branch
            if names & PROTECTED_BRANCHES:
                return deny("Renaming or copying onto a protected branch moves it; that needs the "
                            "owner.")
            return ask("Renaming or copying a branch.")
        return OK
    if sub == "tag" and (flags & {"-d", "--delete", "-f", "--force"} or re.search(r"[df]", short)):
        return deny("Deleting or moving tags needs the owner.")
    if sub == "update-ref":
        if "-d" in flags or any(re.search(r"refs/heads/(main|master)$", p) for p in pos):
            return deny("git update-ref deletes or moves a protected branch.")
        return ask("git update-ref moves a ref directly.")
    if sub == "reflog" and pos[:1] in (["expire"], ["delete"]):
        return deny("Expiring the reflog removes the way back.")
    if sub in {"gc", "prune"} and any(a.startswith("--prune") or a == "--aggressive" for a in args):
        return ask("git gc --prune removes unreachable commits.")
    if sub == "fetch" and any(re.search(r":(refs/heads/)?(main|master)$", p) for p in pos):
        return deny("This fetch writes a protected branch.")
    if sub == "worktree" and pos[:1] == ["add"]:
        reset = resets_protected("worktree", args[args.index("add") + 1:])
        if reset:
            return deny(f"git worktree add -B {reset} resets a protected branch; that needs the "
                        "owner.")
        note_worktree_add(args[args.index("add") + 1:], cwd, ctx)
    if sub in {"merge", "pull"}:
        if flags & {"--abort", "--quit"}:
            return OK
        if branch in PROTECTED_BRANCHES:
            return deny(f"git {sub} into {branch}: merges into main need the owner's OK. "
                        "Stop and ask the owner.")
        if not branch or not cwd_known or other_repo:
            return ask(f"git {sub}: the guard could not read the current branch.")
        if sub == "pull":
            return ask("git pull (no remote is configured).")
        return OK  # a merge into a work branch, or one this command made (git worktree add -b)
    if sub in {"commit", "cherry-pick", "revert", "am"}:
        verdicts = []
        if "--amend" in flags:
            verdicts.append(ask("git commit --amend rewrites the last commit."))
        elif branch in PROTECTED_BRANCHES:
            verdicts.append(ask(f"You are on {branch}. Work happens on a branch; the owner merges."))
        if sub == "commit":
            verdicts.append(check_commit_ids(args, cwd, ctx))
        return worst(verdicts)
    if sub == "worktree" and pos[:1] == ["remove"] and flags & {"-f", "--force"}:
        return ask("git worktree remove --force drops uncommitted work in that worktree.")
    if sub == "config" and not (flags & {"--get", "--list", "-l", "--get-all", "--show-origin",
                                         "--get-regexp"}):
        return ask("git config changes repository settings.") if len(pos) > 1 else OK
    if sub == "remote" and pos[:1] in (["add"], ["set-url"], ["remove"], ["rm"], ["rename"]):
        return ask("Changing git remotes.")
    if sub == "add":
        return check_git_add(args, flags, pos, short, cwd)
    if sub == "mv":
        top = run_git(cwd, "rev-parse", "--show-toplevel").strip()
        for src in pos[:-1]:
            rel = os.path.relpath(resolve(src, cwd), top) if top else src
            if owner_hit(rel):
                return deny(f"git mv {src} would move the owner's untracked "
                            f"{', '.join(owner_hit(rel))} with it. Move tracked files one by one.")
    return OK


def check_git_add(args: list[str], flags: set[str], pos: list[str], short: str,
                  cwd: Path) -> Verdict:
    if flags & {"-f", "--force"} or "f" in short:
        return deny("git add -f stages ignored files (for example .env). Name tracked files only.")
    specs = list(pos)
    if flags & {"-A", "--all", "--no-ignore-removal"} or "A" in short or not specs and not (
            flags & {"-u", "--update"} or "u" in short):
        specs.append(":/")
    if (flags & {"-u", "--update"} or "u" in short) and not specs:
        return OK
    top = run_git(cwd, "rev-parse", "--show-toplevel").strip()
    for spec in specs:
        files = untracked(cwd, spec)
        owner = [f for f in files if owner_hit(f)]
        if owner:
            return deny("This would stage owner files that must stay untracked: "
                        f"{', '.join(owner[:5])}. Name each file you mean to add.")
        wide = spec in {".", "./", ":/", ":", "*", ":/*"} or (top and str(resolve(spec, cwd)) == top)
        if files and wide:
            return deny(f"git add {spec} would stage untracked files ({', '.join(files[:5])}"
                        f"{' ...' if len(files) > 5 else ''}). Name each file you mean to add.")
    return OK


# ----------------------------------------------------------------------------------------------
# Commit ID check: a commit that changes code cites an FR or TR ID (.claude/rules/spec-driven.md).
# It asks, never denies: the owner can still confirm a commit without an ID.
# ----------------------------------------------------------------------------------------------

# Code paths: Java sources, tests, Flyway migrations (src/main/resources/db/migration/ is inside
# src/main/), the build script, and the version catalog and wrapper under gradle/.
CODE_PATHS = ("src/main/", "src/test/", "build.gradle/", "gradle/", "scripts/")
REQ_ID_RE = re.compile(r"\b[FT]R-\d+\b")
COMMIT_VALUE_LONG = {"--message", "--file", "--reuse-message", "--reedit-message", "--fixup",
                     "--squash", "--template", "--author", "--date", "--cleanup", "--trailer",
                     "--pathspec-from-file"}
# Where the message comes from: m (text), F (file), C (another commit).
COMMIT_MSG_LONG = {"--message": "m", "--file": "F", "--reuse-message": "C",
                   "--reedit-message": "C", "--fixup": "C", "--squash": "C"}
DIFF_NAMES = ("diff", "--name-only", "-z", "--no-renames", "--no-ext-diff", "--no-textconv")


def parse_commit(args: list[str]) -> tuple[list[tuple[str, str]], set[str], list[str]]:
    """git commit arguments: message sources, flags, pathspecs."""
    msgs: list[tuple[str, str]] = []
    flags: set[str] = set()
    paths: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--":
            paths += args[i:]
            break
        if a.startswith("--"):
            key, eq, val = a.partition("=")
            if key in COMMIT_VALUE_LONG and not eq:
                val = args[i] if i < len(args) else ""
                i += 1
            flags.add(key)
            if key in COMMIT_MSG_LONG:
                msgs.append((COMMIT_MSG_LONG[key], val))
            continue
        if a.startswith("-") and len(a) > 1:
            for k, ch in enumerate(a[1:], 1):
                if ch in "mFCct":
                    val = a[k + 1:]
                    if not val:
                        val = args[i] if i < len(args) else ""
                        i += 1
                    if ch != "t":
                        msgs.append(("C" if ch == "c" else ch, val))
                    break
                flags.add("-" + ch)
                if ch in "Su":  # -S<keyid>, -u<mode>: the rest is the value
                    break
            continue
        paths.append(a)
    return msgs, flags, paths


def git_names(cwd: Path, *args: str) -> list[str]:
    out = git_out(cwd, *args)
    if out is None:
        raise LookupError("git could not list the files")
    return [f for f in out.split("\0") if f]


def spec_names(cwd: Path, specs: list[str], untracked_too: bool) -> list[str]:
    """Files (relative to the repository top) that these pathspecs stage or commit."""
    top = git_out(cwd, "rev-parse", "--show-toplevel")
    if top is None:
        raise LookupError("not a git repository")
    names: list[str] = []
    for spec in specs:
        if has_var(spec):
            spec = ":/"
        if not spec.startswith(":"):
            names.append(os.path.relpath(resolve(spec, cwd), top.strip()))  # git rm, git mv
        names += git_names(cwd, *DIFF_NAMES, "--", spec)
        names += git_names(cwd, *DIFF_NAMES, "--cached", "--", spec)
        if untracked_too:
            names += git_names(cwd, "ls-files", "--others", "--exclude-standard", "--full-name",
                               "-z", "--", spec)
    return names


def commit_files(flags: set[str], paths: list[str], cwd: Path, ctx: Ctx) -> list[str]:
    """Files the commit will hold: the index, plus `-a`, pathspecs, and `git add/rm/mv` earlier in
    the same command (they run before the commit, after this hook)."""
    if paths and not flags & {"-i", "--include"}:
        return spec_names(cwd, paths, False)  # --only (the default with paths): just those paths
    names = git_names(cwd, *DIFF_NAMES, "--cached") + spec_names(cwd, paths, False)
    if flags & {"-a", "--all", "--pathspec-from-file"}:
        names += git_names(cwd, *DIFF_NAMES)
    for scwd, sub, sargs in ctx.stage_specs:
        specs = sargs[sargs.index("--") + 1:] if "--" in sargs else [
            a for a in sargs if not a.startswith("-")]
        names += spec_names(scwd, specs or [":/"], sub == "add")
    return names


def commit_message(msgs: list[tuple[str, str]], cwd: Path, ctx: Ctx) -> str | None:
    """The message text, or None when it cannot be read. A message built by $(...), `...` or a
    heredoc on stdin (-F -) is checked against the whole command text."""
    parts = []
    for kind, val in msgs:
        if kind == "m":
            parts.append(ctx.raw if has_var(val) else val)
        elif kind == "F":
            if val == "-" or has_var(val):
                parts.append(ctx.raw)
                continue
            p = resolve(val, cwd)
            if is_secret_path(p):
                return None
            try:
                with open(p, "rb") as f:
                    parts.append(f.read(1_000_000).decode("utf-8", errors="replace"))
            except OSError:
                return None
        else:
            ref = val.split(":", 1)[1] if val.startswith(("amend:", "reword:")) else val
            out = git_out(cwd, "log", "-1", "--format=%B", "--end-of-options", ref)
            if out is None:
                return None
            parts.append(out)
    return "\n".join(parts) if parts else None


def check_commit_ids(args: list[str], cwd: Path, ctx: Ctx) -> Verdict:
    msgs, flags, paths = parse_commit(args)
    if flags & {"--dry-run", "-h", "--help"}:
        return OK
    try:
        names = commit_files(flags, paths, cwd, ctx)
    except LookupError:
        return ask("Commit ID check: the staged files could not be read. Make sure the message "
                   "cites an FR-n or TR-n ID if the commit changes code.")
    code = sorted({n for n in names if (n + "/").startswith(CODE_PATHS)})
    if not code:
        return OK
    if not msgs and "--amend" in flags:
        msgs = [("C", "HEAD")]
    message = commit_message(msgs, cwd, ctx)
    if message is not None and REQ_ID_RE.search(message):
        return OK
    shown = ", ".join(code[:3]) + (" ..." if len(code) > 3 else "")
    return ask(f"This commit changes code ({shown}) but its message cites no FR-n or TR-n ID "
               "(.claude/rules/spec-driven.md: no FR ID, no code). Add the ID to the message.")


# ----------------------------------------------------------------------------------------------
# Files: rm, mv, find -delete, docker, registries, installs
# ----------------------------------------------------------------------------------------------

PROTECTED_IN_REPO = (".git", ".claude", ".superpowers", "src", "docs", "ai", "scripts", "gradle")


def target_paths(t: str, ctx: Ctx) -> list[Path] | Verdict:
    """Every path a file argument can mean, given each directory a `cd` may have moved to."""
    if has_var(expand(t)):
        return ask(f"Target {t!r} cannot be checked (variable).")
    if ctx.cwd_unknown and not expand(t).startswith("/"):
        return ask(f"Target {t!r} is relative to a directory the guard cannot know (cd $VAR/-).")
    paths: list[Path] = []
    for cwd in ctx.cwds:
        found = expand_glob(t, cwd)
        if found is None:
            return ask(f"{t!r} matches too many files to check.")
        paths += found
    return paths


def check_rm(words: list[str], ctx: Ctx) -> Verdict:
    pos, fl = split_flags(words[1:], set())
    short = "".join(k[1:] for k in fl if not k.startswith("--"))
    recursive = "r" in short or "R" in short or "--recursive" in fl
    repo = ctx.repo()
    verdicts = []
    for t in pos:
        paths = target_paths(t, ctx)
        if isinstance(paths, Verdict):
            verdicts.append(paths)
            continue
        for p in paths:
            verdicts.append(rm_one(p, repo, recursive))
            if verdicts[-1].decision == "deny":
                return verdicts[-1]
    return worst(verdicts)


def rm_one(p: Path, repo: Path, recursive: bool) -> Verdict:
    if inside(p, scratch_root()):
        return OK
    if inside(repo, p) or p in (Path("/"), HOME, WEB_BLOCK):
        return deny(f"rm would delete {p}, which holds the repository or the owner's files.")
    if not inside(p, repo):
        return deny(f"rm outside the repository ({p}). Ask the owner.") if recursive else \
            ask(f"rm outside the repository ({p}).")
    rel = os.path.relpath(p, repo)
    if owner_hit(rel):
        return deny(f"rm {rel} would delete the owner's untracked {', '.join(owner_hit(rel))}; "
                    "never delete it.")
    top = rel.split("/")[0]
    if recursive and (top in PROTECTED_IN_REPO and rel == top or top in {".git", ".superpowers"}):
        return deny(f"rm -r {rel} deletes a core repository folder.")
    if recursive and run_git(repo, "ls-files", "--", rel).strip():
        return deny(f"rm -r {rel} deletes tracked files; use git rm for tracked files.")
    return OK


def check_mv(words: list[str], ctx: Ctx) -> Verdict:
    pos, fl = split_flags(words[1:], {"-t", "--target-directory", "-S", "--suffix"})
    sources = pos if ("-t" in fl or "--target-directory" in fl) else pos[:-1]
    repo = ctx.repo()
    for t in sources:
        paths = target_paths(t, ctx)
        if isinstance(paths, Verdict):
            return paths
        for p in paths:
            if inside(repo, p) or p in (Path("/"), HOME, WEB_BLOCK):
                return deny(f"mv would move {p}, which holds the repository or the owner's files.")
            if inside(p, repo):
                rel = os.path.relpath(p, repo)
                if owner_hit(rel):
                    return deny(f"mv {rel} would move the owner's untracked "
                                f"{', '.join(owner_hit(rel))}; never move it.")
                if rel.split("/")[0] in {".git", ".superpowers"} or rel in PROTECTED_IN_REPO:
                    return deny(f"mv {rel} moves a core repository folder; use git mv for files.")
    return OK


def check_find(words: list[str], ctx: Ctx) -> Verdict:
    if not ({"-delete", "-exec", "-execdir", "-ok", "-okdir"} & set(words)):
        return OK
    if not ("-delete" in words or re.search(r"\b(rm|shred|unlink|mv)\b", " ".join(words))):
        return OK
    repo = ctx.repo()
    roots: list[str] = []
    for w in words[1:]:
        if w.startswith("-") or w in {"(", "!"}:
            break
        roots.append(w)
    for r in roots or ["."]:
        paths = target_paths(r, ctx)
        if isinstance(paths, Verdict):
            return paths
        for p in paths:
            if not (inside(p, repo) or inside(p, scratch_root())) or (inside(repo, p) and p != repo):
                return deny(f"find ... -delete outside the repository ({p}).")
    return ask("find ... -delete removes files; check the match list first (run without -delete).")


def local_registry(ref: str) -> bool:
    return bool(re.match(r"^(localhost|127\.0\.0\.1|kind-registry)(:\d+)?/", ref))


def mount_sources(words: list[str]) -> list[str]:
    """The host side of each -v/--volume and --mount (src=/source=) of a container command."""
    found: list[str] = []
    for i, w in enumerate(words):
        key, val = w, None
        if w in {"-v", "--volume", "--mount"} and i + 1 < len(words):
            val = words[i + 1]
        elif w.startswith(("--volume=", "--mount=")):
            key, val = w.split("=", 1)
        elif w.startswith("-v") and len(w) > 2:
            key, val = "-v", w[2:].lstrip("=")
        if val is None:
            continue
        if key == "--mount":
            try:  # docker reads --mount as one CSV record: "source=/x",target=/y
                fields = next(csv.reader([val]), [])
            except csv.Error:
                fields = val.split(",")
            found += [v for k, _, v in (f.partition("=") for f in fields)
                      if k.strip() in {"src", "source"}]
        else:
            found.append(val.split(":", 1)[0])
    return found


def check_docker(words: list[str], cwd: Path) -> Verdict:
    pos, fl = split_flags(words[1:], {"-t", "--tag", "-f", "--file", "--platform", "-o",
                                      "--output", "--build-arg", "--name", "-e", "--env", "-v",
                                      "--volume", "-p", "--publish", "--network", "-w"})
    verb = pos[0] if pos else ""
    rest = pos[1:]
    if {"run", "create"} & set(pos[:2]):
        for src in mount_sources(words[1:]):
            host = re.sub(r"\$\{PWD\}|\$PWD(?![A-Za-z0-9_])", lambda _: str(cwd), expand(src))
            if not host:
                continue
            if has_var(host):
                return ask(f"The container mounts {src}; the guard cannot see that folder. "
                           "Use $PWD, $(pwd) or a written-out path.")
            p = resolve(host, cwd)
            if is_secret_path(p) or any(inside(d, p) for d in [*SECRET_DIRS, *SECRET_FILES]):
                return deny(f"The container mounts {src}, which is or holds a secret folder "
                            "(~/.kube, ~/.ssh, cred, ...). A container must not get a kubeconfig "
                            "or credentials.")
    if "prune" in pos[:3] or (verb == "system" and rest[:1] == ["prune"]):
        return deny("docker prune removes images, volumes or caches the owner and Kind need.")
    if verb == "push" or (verb in {"buildx", "build"} and ("--push" in fl or "type=registry" in
                                                           fl.get("-o", fl.get("--output", "")))):
        refs = rest[:1] if verb == "push" else [v for k, v in fl.items() if k in {"-t", "--tag"}]
        if refs and all(local_registry(r) for r in refs):
            return OK
        return deny("Pushing an image to a remote registry is the owner's job (Jenkins builds the "
                    "real images).")
    if verb == "login":
        return ask("docker login stores registry credentials.")
    if verb in {"rm", "rmi", "stop", "kill"} or (verb in {"volume", "network", "image",
                                                           "container"} and rest[:1] in (["rm"],)):
        return ask(f"docker {verb} removes or stops something that may belong to the owner "
                   "(for example the owner's rabbitmq container).")
    if verb == "compose" and "down" in rest:
        return ask("docker compose down stops services; -v also deletes volumes.")
    return OK


def check_crane(words: list[str]) -> Verdict:
    pos, _ = split_flags(words[1:], {"--platform", "-t", "--tag"})
    verb = pos[0] if pos else ""
    if verb in {"push", "copy", "cp", "delete", "tag", "mutate", "append", "rebase", "flatten"}:
        refs = [p for p in pos[1:] if "/" in p or ":" in p]
        if refs and all(local_registry(r) for r in refs[-1:]):
            return OK
        return deny(f"crane {verb} writes to a remote registry; the owner does it.")
    if verb == "auth":
        return deny("crane auth handles registry credentials.")
    return OK


INSTALLERS = {"pip", "pip3", "pipx", "npm", "pnpm", "yarn", "cargo", "go", "gem", "pacman", "yay",
              "paru", "apt", "apt-get", "dnf", "brew", "snap", "flatpak", "sdk"}


def check_install(name: str, words: list[str]) -> Verdict:
    pos = [w for w in words[1:] if not w.startswith("-")]
    verb = pos[0] if pos else ""
    if name in {"pacman", "yay", "paru"} and any(w.startswith("-S") or w.startswith("-R")
                                                  for w in words[1:]):
        return deny("System packages are the owner's job.")
    if verb in {"install", "i", "add", "get"}:
        return deny(f"{name} {verb} installs software outside the pinned toolchain. Java comes "
                    "from the Gradle toolchain; dependencies go through gradle/libs.versions.toml "
                    "and gradle.lockfile (open-source licence, 14-day rule, scripts/osv-scan.sh).")
    return OK


# Gradle flags that rewrite the dependency lock or verification files.
GRADLE_LOCK_FLAGS = ("--write-locks", "--update-locks", "--write-verification-metadata", "-M")


def check_gradle(words: list[str]) -> Verdict:
    """./gradlew (or gradle) tasks run normally; a lock or verification rewrite asks the owner."""
    hits = [w for w in words[1:] if w.split("=", 1)[0] in GRADLE_LOCK_FLAGS]
    if hits:
        return OK  # pins are approved at the design or plan gate; OSV and age checks guard them
    return OK


TERRAFORM_WRITE = {"apply", "destroy", "import", "taint", "untaint", "state", "force-unlock"}


# ----------------------------------------------------------------------------------------------
# Generic word checks: secret paths, protected Claude config
# ----------------------------------------------------------------------------------------------

READ_ONLY_META = {"ls", "stat", "test", "[", "[[", "realpath", "readlink", "dirname", "basename",
                  "chmod", "file", "cd", "pushd"}
WRITERS = {"sed", "tee", "cp", "mv", "rm", "ln", "truncate", "dd", "install", "perl", "awk",
           "chmod", "chown", "touch", "rsync", "shred", "unlink"}
SECRET_DENY = ("is a secret file or folder (credentials, keys, .env, kubeconfig). Never read, print, "
               "copy or commit it.")


SED_FLAGS = set("nErsuz")  # short options that only change how sed reads or prints
SED_LONG = {"--quiet", "--silent", "--regexp-extended", "--separate", "--null-data", "--unbuffered",
            "--posix", "--debug", "--sandbox", "--zero-terminated"}
# Every GNU sed long option: getopt takes any unambiguous prefix of one (--expr is --expression).
SED_ALL_LONG = SED_LONG | {"--expression", "--file", "--in-place", "--line-length",
                           "--follow-symlinks", "--binary", "--help", "--version"}


def sed_long(key: str) -> str | None:
    """The full long option a word names, or None when it is unknown or ambiguous."""
    if key in SED_ALL_LONG:
        return key
    hits = [o for o in SED_ALL_LONG if o.startswith(key)]
    return hits[0] if len(hits) == 1 else None
SED_SIMPLE = set("pPlLqQdDnNgGhHxz=F{}!")  # script commands that neither write, read nor run


class SedFacts:
    """What one sed call does: reads only, or writes, reads or runs something more."""

    def __init__(self) -> None:
        self.reads_only = True             # False: a write, a script file, or a script we cannot read
        self.writes: list[str] = []        # files a w or W command (or s///w) writes
        self.reads: list[str] = []         # files an r or R command reads
        self.runs = False                  # an e command or s///e runs a shell command


def sed_words(words: list[str], facts: SedFacts) -> list[str]:
    """The scripts of a sed call (-e values, or the first plain word); unknown options mark a writer."""
    scripts: list[str] = []
    plain: list[str] = []
    i, options_end = 1, False
    while i < len(words):
        w = words[i]
        i += 1
        if options_end or w == "-" or not w.startswith("-"):
            plain.append(w)
            continue
        if w == "--":
            options_end = True
            continue
        if w.startswith("--"):
            key, eq, val = w.partition("=")
            key = sed_long(key) or ""
            if key in SED_LONG and not eq:
                continue
            if key in {"--expression", "--line-length"}:
                if not eq:
                    val = words[i] if i < len(words) else ""
                    i += 1
                if key == "--expression":
                    scripts.append(val)
                elif not val.isdigit():
                    facts.reads_only = False
                continue
            facts.reads_only = False  # --in-place, --file, an abbreviation, or an unknown option
            continue
        for j, ch in enumerate(w[1:], start=1):
            if ch in SED_FLAGS:
                continue
            if ch in "el":
                val = w[j + 1:]
                if not val:
                    val = words[i] if i < len(words) else ""
                    i += 1
                if ch == "e":
                    scripts.append(val)
                elif not val.isdigit():
                    facts.reads_only = False
                break
            facts.reads_only = False  # -i in any form, -f, or an unknown option
            break
    if not scripts:
        if not plain:
            facts.reads_only = False
            return []
        scripts.append(plain[0])
    return scripts


def sed_skip_regex(s: str, k: int, delim: str) -> int:
    """The index just past the closing delimiter, or -1 when there is none."""
    while k < len(s):
        if s[k] == "\\":
            k += 2
            continue
        if s[k] == delim:
            return k + 1
        if s[k] == "\n":
            return -1
        k += 1
    return -1


def sed_rest_of_line(s: str, k: int) -> tuple[str, int]:
    end = s.find("\n", k)
    end = len(s) if end < 0 else end
    return s[k:end].strip(), end


def sed_script(s: str, facts: SedFacts) -> None:
    """Read a sed script; anything it cannot read for sure marks a writer."""
    k = 0
    while k < len(s):
        ch = s[k]
        if ch in " \t\n;":
            k += 1
            continue
        if ch.isdigit() or ch in "$,+~":
            k += 1
            continue
        if ch == "/" or (ch == "\\" and k + 1 < len(s)):
            delim = "/" if ch == "/" else s[k + 1]
            k = sed_skip_regex(s, k + (1 if ch == "/" else 2), delim)
            if k < 0:
                facts.reads_only = False
                return
            while k < len(s) and s[k] in "IM":
                k += 1
            continue
        if ch == "#":
            _, k = sed_rest_of_line(s, k)
            continue
        if ch in SED_SIMPLE:
            k += 1
            continue
        if ch in "btT:aic":
            _, k = sed_rest_of_line(s, k + 1) if ch in "aic" else sed_label(s, k + 1)
            continue
        if ch in "sy":
            if k + 1 >= len(s) or s[k + 1] in "\\\n":
                facts.reads_only = False
                return
            delim = s[k + 1]
            k = sed_skip_regex(s, k + 2, delim)
            k = sed_skip_regex(s, k, delim) if k >= 0 else -1
            if k < 0:
                facts.reads_only = False
                return
            if ch == "y":
                continue
            while k < len(s) and (s[k] in "gpiImM" or s[k].isdigit()):
                k += 1
            if k < len(s) and s[k] == "e":
                facts.runs = True
                facts.reads_only = False
                k += 1
            if k < len(s) and s[k] == "w":
                target, k = sed_rest_of_line(s, k + 1)
                facts.writes.append(target)
                facts.reads_only = False
            continue
        if ch in "wWrR":
            target, k = sed_rest_of_line(s, k + 1)
            (facts.writes if ch in "wW" else facts.reads).append(target)
            facts.reads_only = False
            continue
        if ch == "e":
            facts.runs = True
        facts.reads_only = False  # e, v or a command this reader does not know
        return


def sed_label(s: str, k: int) -> tuple[str, int]:
    end = k
    while end < len(s) and s[end] not in ";\n":
        end += 1
    return s[k:end].strip(), end


def sed_facts(words: list[str]) -> SedFacts:
    facts = SedFacts()
    for script in sed_words(words, facts):
        sed_script(script, facts)
    return facts


# Shell startup files: a line there runs before every command the guard checks.
RC_FILES = {HOME / n for n in (".bashrc", ".bash_profile", ".bash_login", ".profile", ".zshrc",
                               ".zprofile", ".zshenv", ".zlogin", ".bash_aliases",
                               ".config/fish/config.fish")}
# Folders whose every file a shell loads at start.
RC_DIRS = [HOME / n for n in (".bashrc.d", ".zshrc.d", ".oh-my-zsh/custom", ".config/fish/conf.d",
                              ".config/fish/functions")]


def is_rc(p: Path) -> bool:
    return p in RC_FILES or any(inside(p, d) for d in RC_DIRS)
RC_ASK = ("This changes a shell startup file (~/.bashrc, ~/.zshrc, ~/.profile, ...). An alias, "
          "function or variable there hides commands from the guard; the owner approves.")
EDITORS = {"vi", "vim", "nvim", "nano", "emacs", "ed", "ex", "micro", "code", "gedit", "kate"}


def claude_config(p: Path, repo: Path) -> bool:
    rel = os.path.relpath(p, repo)
    return rel in {".claude/settings.json", ".claude/settings.local.json"} or rel.startswith(
        ".claude/hooks/")


CONSTITUTION = ".claude/docs/CONSTITUTION.md"
CONSTITUTION_ASK = ("This changes the constitution (.claude/docs/CONSTITUTION.md). Only the owner amends "
                    "it; the owner must approve.")
IN_PLACE = {"perl", "awk"}  # write only with an in-place flag (sed: sed_facts)


def constitution(p: Path, repo: Path) -> bool:
    return os.path.relpath(p, repo) == CONSTITUTION


def writes_constitution(name: str, words: list[str], is_output: bool) -> bool:
    """A shell write: a redirect, a writer command, a sed that is not a plain read (sed_facts), an
    in-place perl/awk, or git mv/rm."""
    if is_output:
        return True
    if name == "sed":
        return not sed_facts(words).reads_only
    if name in IN_PLACE:
        return any(w.startswith("-i") or w in {"-pi", "inplace"} for w in words[1:])
    if name == "git":
        return words[1:2] in (["mv"], ["rm"])
    return name in WRITERS


def check_words(name: str, seg: Segment, ctx: Ctx) -> Verdict:
    repo = ctx.repo()
    skip_next = False
    verdicts = []
    candidates: list[tuple[str, bool, bool]] = [(w, False, False) for w in seg.words[1:]]
    candidates += [(t, True, out) for t, out in seg.redirects]
    sed = sed_facts(seg.words) if name == "sed" else None
    if sed is not None:
        candidates += [(t, True, True) for t in sed.writes] + [(t, True, False) for t in sed.reads]
        if sed.runs:
            verdicts.append(ask("sed e runs a shell command the guard cannot see."))
    meta_ok = name in READ_ONLY_META or (name == "git" and seg.words[1:2] in (
        ["check-ignore"], ["ls-files"], ["status"]))
    for w, is_redirect, is_output in candidates:
        if skip_next:
            skip_next = False
            continue
        if w in {"--kubeconfig", "--kube-context"} and (name in CLUSTER_TOOLS
                                                        or w == "--kube-context"):
            skip_next = True
            continue
        if w.startswith("-"):
            key, eq, val = w.partition("=")
            if not eq or (key == "--kubeconfig" and name in CLUSTER_TOOLS):
                continue
            w = val  # --env-file=.env, --from-file=x
        if not w:
            continue
        w = expand(w)
        if has_var(w):
            # Only the literal file name can be checked: $DIR/.env is still a .env file.
            base = w.rstrip("/").rsplit("/", 1)[-1]
            if not has_var(base) and SECRET_NAME_RE.match(base) and not meta_ok:
                verdicts.append(deny(f"{w} {SECRET_DENY}"))
            continue
        for cwd in ctx.cwds:
            paths = expand_glob(w, cwd)
            if paths is None:
                verdicts.append(ask(f"{w!r} matches too many files to check."))
                break
            is_glob = any(ch in w for ch in "*?[")
            for p in paths:
                looks_like_path = ("/" in w or w.startswith(".") or is_redirect or is_glob
                                   or exists(p))
                if not looks_like_path:
                    continue
                if is_secret_path(p) and (is_redirect or not meta_ok):
                    verdicts.append(deny(f"{w} {SECRET_DENY}"))
                sed_reads = sed is not None and sed.reads_only
                if claude_config(p, repo) and (is_output or (name in WRITERS and not sed_reads)):
                    verdicts.append(ask("This changes the Claude guard configuration "
                                        "(.claude/settings*.json or .claude/hooks/). The owner "
                                        "must approve."))
                if constitution(p, repo) and writes_constitution(name, seg.words, is_output):
                    verdicts.append(ask(CONSTITUTION_ASK))
                if is_rc(p) and (is_output or name in EDITORS
                                      or (name in WRITERS and not sed_reads)):
                    verdicts.append(ask(RC_ASK))
    return worst(verdicts)


# ----------------------------------------------------------------------------------------------
# Segment and command analysis
# ----------------------------------------------------------------------------------------------

WRAPPERS = {"command", "builtin", "nohup", "noglob", "busybox"}
# Shell keywords and group braces in front of a command (if x; then kubectl ...; fi).
KEYWORDS = {"{", "}", "!", "if", "then", "elif", "else", "fi", "do", "done", "while", "until"}
SYSTEMD_RUN_VALUE = {"--unit", "--property", "--description", "--slice", "--setenv", "--uid",
                     "--gid", "--nice", "--working-directory", "--machine", "--host",
                     "--on-active", "--on-boot", "--on-startup", "--on-unit-active",
                     "--on-unit-inactive", "--on-calendar", "--timer-property", "--path-property",
                     "--socket-property", "--service-type", "--capsule"}
# Wrappers that take options: (one-letter options with a value, long options with a value).
OPTION_WRAPPERS = {
    "time": ("of", {"--output", "--format"}),
    "setsid": ("", set()),
    "exec": ("a", set()),
    "chronic": ("", set()),
    "unbuffer": ("", set()),
    "systemd-run": ("upEMH", SYSTEMD_RUN_VALUE),
    "stdbuf": ("ioe", {"--input", "--output", "--error"}),
    "strace": ("abeEIoOpPsSuX", {"--output", "--attach", "--user", "--trace", "--signal"}),
    "ltrace": ("aAeFlnopsuwx", {"--output", "--library", "--user"}),
    "unshare": ("SGRw", {"--setuid", "--setgid", "--root", "--wd", "--map-user", "--map-group",
                         "--map-users", "--map-groups", "--propagation"}),
    "nsenter": ("tSG", {"--target", "--setuid", "--setgid"}),
}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SSH_VALUE = {"-p", "-i", "-l", "-o", "-F", "-J", "-L", "-R", "-D", "-W", "-b", "-c", "-E", "-e",
             "-m", "-O", "-Q", "-S", "-w", "-B"}
KUBE_NAMES = {"kubectl", "oc", "k", "kubecolor"}
CLUSTER_TOOLS = KUBE_NAMES | {"helm", "kind", "kubectx", "kubens", "istioctl", "cilium", "argocd",
                              "flux"}
# A command the guard cannot read only asks when the text names one of these.
CLUSTER_OR_PROTECTED = re.compile(
    r"(?<![\w./-])(kubectl[\w.-]*|oc|k|kubecolor|helm|kind|istioctl|cilium|argocd|flux|doctl|"
    r"kubectx|kubens|terraform|tofu|git|rm|mv)(?![\w-])|\.kube\b|kubeconfig|\.env\b|\.ssh\b|"
    r"secure-key|/cred\b|\.claude\b|CONSTITUTION")


# A word that is a cluster tool. A command the guard does not know asks when a later word is
# one (tmux new "kubectl ...", rlwrap kubectl ...), unless it only reads or prints text.
CLUSTER_WORD = re.compile(r"^(kubectl([.-].*)?|kubecolor|oc|helm|kind|istioctl|cilium|flux|argocd|"
                          r"kubectx|kubens|k9s|stern|doctl)$")
NAMES_A_TOOL_SAFELY = {
    "echo", "printf", "grep", "egrep", "fgrep", "rg", "ag", "ack", "cat", "less", "more", "head",
    "tail", "which", "type", "whereis", "whatis", "man", "info", "tldr", "apropos", "help", "ls",
    "stat", "file", "wc", "cd", "pushd", "test", "[", "[[", "true", "false", ":", "read", "sort",
    "uniq", "cut", "tr", "jq", "yq", "awk", "sed", "diff", "column", "basename", "dirname",
    "realpath", "readlink", "hash", "complete", "compgen", "history", "pgrep", "ps", "unalias",
    "unset", "mkdir", "touch", "make", "for", "select", "case", "in",
}


def skip_options(words: list[str], short_values: str, long_values: set[str]) -> list[str]:
    """The words after the options of a wrapper (words starts after the wrapper's name)."""
    i = 0
    while i < len(words):
        w = words[i]
        if w == "--":
            return words[i + 1:]
        if not w.startswith("-") or w == "-":
            break
        if w.startswith("--"):
            i += 2 if w in long_values else 1
            continue
        letters = w[1:]
        hit = next((k for k, ch in enumerate(letters) if ch in short_values), None)
        i += 2 if hit == len(letters) - 1 else 1
    return words[i:]


def strip_wrappers(words: list[str], env: dict[str, str], marks: set[str]) -> list[str]:
    changed = True
    while words and changed:
        changed = False
        while words and ASSIGN.match(words[0]):
            k, v = words[0].split("=", 1)
            env[k] = v
            words = words[1:]
            changed = True
        if not words:
            break
        name = os.path.basename(words[0])
        if words[0] in KEYWORDS:
            words = words[1:]
            changed = True
        elif words[0] == "function":
            words = words[2:]
            changed = True
        elif words[0] == "coproc":
            # coproc NAME { ...; } names the coprocess; coproc cmd ... runs cmd.
            words = words[2:] if words[2:3] == ["{"] else words[1:]
            changed = True
        elif name == "taskset":
            rest = skip_options(words[1:], "", set())
            words = rest[1:]  # the CPU mask or list
            changed = True
        elif name in OPTION_WRAPPERS:
            words = skip_options(words[1:], *OPTION_WRAPPERS[name])
            changed = True
        elif name == "chroot":
            words = skip_options(words[1:], "", set())[1:]
            changed = True
        elif name == "flock":
            rest = skip_options(words[1:], "wE", {"--timeout", "--wait", "--conflict-exit-code"})
            rest = rest[1:]  # the lock file
            if rest[:1] in (["-c"], ["--command"]):
                words = ["sh", "-c", rest[1] if len(rest) > 1 else ""]
            else:
                words = rest
            changed = True
        elif name == "script":
            args, cmd = words[1:], None
            for k, w in enumerate(args):
                if w.startswith("--command="):
                    cmd = w.split("=", 1)[1]
                elif w == "--command" or (w.startswith("-") and not w.startswith("--")
                                          and "c" in w[1:]):
                    tail = "" if w == "--command" else w[w.index("c", 1) + 1:]
                    cmd = tail or (args[k + 1] if k + 1 < len(args) else "")
                if cmd is not None:
                    break
            return ["sh", "-c", cmd] if cmd is not None else []
        elif name == "parallel":
            rest = skip_options(words[1:], "jNSadCI", {"--jobs", "--sshlogin", "--arg-file",
                                                      "--delimiter", "--colsep", "--joblog",
                                                      "--results", "--tmpdir", "--replace",
                                                      "--timeout", "--retries", "--halt"})
            cut = next((k for k, w in enumerate(rest) if w.startswith(":::")), len(rest))
            rest = rest[:cut]
            if len(rest) == 1 and " " in rest[0]:
                return ["sh", "-c", "xargs " + rest[0]]
            words = rest
            marks.add("xargs")
            changed = True
        elif name in WRAPPERS:
            if name == "command" and words[1:2] and words[1] in {"-v", "-V"}:
                return []
            words = words[1:]
            while name == "command" and words and words[0] == "-p":
                words = words[1:]
            changed = True
        elif name == "env":
            words = words[1:]
            while words:
                w0 = words[0]
                if ASSIGN.match(w0):
                    k, v = w0.split("=", 1)
                    env[k] = v
                    words = words[1:]
                elif w0 in {"-S", "--split-string"} and len(words) > 1:
                    words = words[1].split() + words[2:]
                elif w0.startswith("-S") and len(w0) > 2:
                    words = w0[2:].split() + words[1:]
                elif w0.startswith("--split-string="):
                    words = w0.split("=", 1)[1].split() + words[1:]
                elif w0 in {"-u", "--unset", "-C", "--chdir"}:
                    words = words[2:]
                elif w0.startswith("-"):
                    words = words[1:]
                else:
                    break
            changed = True
        elif name in {"nice", "ionice"}:
            words = words[1:]
            while words and words[0].startswith("-"):
                words = words[2:] if words[0] in {"-n", "-c"} else words[1:]
            changed = True
        elif name == "timeout":
            words = words[1:]
            while words and words[0].startswith("-"):
                words = words[2:] if words[0] in {"-s", "-k", "--signal", "--kill-after"} else \
                    words[1:]
            words = words[1:]
            changed = True
        elif name == "watch":
            words = words[1:]
            while words and words[0].startswith("-"):
                words = words[2:] if words[0] in {"-n", "--interval"} else words[1:]
            marks.add("watch")
            changed = True
        elif name == "xargs":
            words = words[1:]
            while words and words[0].startswith("-"):
                words = words[2:] if words[0] in {"-I", "-n", "-P", "-L", "-a", "-d", "-E", "-s",
                                                  "--max-args", "--max-procs"} else words[1:]
            marks.add("xargs")
            changed = True
    return words


DECODE_FLAGS = {"-d", "--decode", "-D"}


def note_facts(name: str, words: list[str], ctx: Ctx) -> None:
    """Facts that only mean something for the whole command (secret decoding, curl | sh)."""
    ctx.names.add(name)
    args = words[1:]
    if name in {"base64", "basenc", "openssl"} and (
            DECODE_FLAGS & set(args) or (name != "openssl" and any(
                re.match(r"^-[a-zA-Z]*d[a-zA-Z]*$", a) for a in args))):
        ctx.decoder = True
    if re.match(r"^python[0-9.]*$", name) and args[:2] == ["-m", "base64"] and "-d" in args:
        ctx.decoder = True
    if any(re.search(r"secret|\.data\b", w, re.I) for w in words):
        ctx.secret_words = True


def reads_script_from_stdin(words: list[str], seg: Segment) -> bool:
    """An interpreter that takes its program from a pipe (curl ... | sh, echo 'git push' | bash)."""
    if not seg.piped_in:
        return False
    args = words[1:]
    if any(a == "-s" or a == "-" for a in args):
        return True
    return not [a for a in args if not a.startswith("-")]


def find_exec_blocks(words: list[str]) -> list[list[str]]:
    """The commands of find -exec/-execdir/-ok/-okdir (up to ; or +)."""
    blocks, i = [], 0
    while i < len(words):
        if words[i] in {"-exec", "-execdir", "-ok", "-okdir"}:
            j = i + 1
            while j < len(words) and words[j] not in {";", "+"}:
                j += 1
            blocks.append(words[i + 1:j])
            i = j
        i += 1
    return [b for b in blocks if b]


def analyse_segment(seg: Segment, ctx: Ctx, depth: int) -> Verdict:
    env: dict[str, str] = {}
    marks: set[str] = set()
    words = strip_wrappers(list(seg.words), env, marks)
    first = os.path.basename(expand(words[0])) if words else ""
    runs_cluster = (first in CLUSTER_TOOLS or bool(CLUSTER_WORD.match(first))) if words else bool(
        CLUSTER_IN_TEXT.search(ctx.raw))
    env_verdict = env_rules(env, ctx.cwd, runs_cluster)
    if env_verdict.decision:
        return env_verdict
    if not words:
        if seg.redirects:
            view = Segment()
            view.redirects = seg.redirects
            view.words = [":"]
            return check_words(":", view, ctx)
        return OK
    if "watch" in marks and len(words) == 1 and " " in words[0]:
        return analyse(words[0], ctx, depth + 1)
    name = os.path.basename(expand(words[0]))
    if has_var(name) and CLUSTER_OR_PROTECTED.search(ctx.raw):
        return ask("The command name is a variable or $'...' quoting, and the command names a "
                   "cluster tool or a protected path. Write the command name out.")
    if name == "alias" and CLUSTER_OR_PROTECTED.search(ctx.raw):
        return ask("An alias that names a cluster tool or a protected path hides the real command "
                   "from the guard. Write the command out.")
    note_facts(name, words, ctx)
    view = Segment()
    view.words, view.redirects = words, seg.redirects
    verdicts = [check_words(name, view, ctx)]

    if name in {"sudo", "doas", "pkexec", "su", "run0"}:
        return deny("Privileged commands are the owner's job.")
    if name in {"cd", "pushd"}:
        args = [w for w in words[1:] if not w.startswith("-") or w == "-"]
        target = expand(args[0]) if args else str(HOME)
        if target == "-" or has_var(target):
            ctx.cwd_unknown = True
        else:
            ctx.cwd = resolve(target, ctx.cwd)
            ctx.cwds.append(ctx.cwd)
    elif name == "popd":
        ctx.cwd_unknown = True
    if "xargs" in marks and name in {"rm", "shred", "unlink", "mv"}:
        verdicts.append(ask(f"xargs {name}: the files come from the pipe, so the guard cannot check "
                            "them. Name the files, or check the list first."))
    if name in SHELLS or re.match(r"^python[0-9.]*$", name):
        script = None
        if name in SHELLS:
            for k, w in enumerate(words[1:], 1):
                if w.startswith("-") and not w.startswith("--") and "c" in w[1:]:
                    script = words[k + 1] if k + 1 < len(words) else ""
                    break
            if script is None and seg.heredoc is not None:
                script = seg.heredoc
        if script is not None:
            verdicts.append(analyse(script, ctx, depth + 1))
        elif reads_script_from_stdin(words, seg):
            ctx.piped_script = True
    if name in SHELLS:
        pass
    elif name == "eval":
        verdicts.append(analyse(" ".join(words[1:]), ctx, depth + 1))
    elif name == "ssh":
        rest, i = words[1:], 0
        while i < len(rest) and rest[i].startswith("-"):
            i += 2 if rest[i] in SSH_VALUE else 1
        remote = rest[i + 1:]
        script = " ".join(remote) if remote else seg.heredoc
        inner = analyse(script, ctx, depth + 1) if script else OK
        verdicts.append(inner if inner.decision == "deny" else ask("ssh runs commands on another "
                                                                  "machine."))
    elif name in {"export", "declare", "typeset", "readonly", "local"}:
        verdicts.append(env_rules(dict(w.split("=", 1) for w in words[1:] if ASSIGN.match(w)),
                                  ctx.cwd, bool(CLUSTER_IN_TEXT.search(ctx.raw))))
    elif name.startswith("kubectl-"):
        verdicts.append(ask(f"{name} is a kubectl plugin; the guard cannot know what it does."))
    elif name in KUBE_NAMES or re.match(r"^kubectl[.0-9]", name):
        verdicts.append(check_kubectl(words, env, ctx.cwd, piped_args="xargs" in marks))
    elif name in {"kubectx", "kubens"}:
        args = [w for w in words[1:] if w not in {"-c", "--current", "-h", "--help"}]
        if args and not local_kind(env.get("KUBECONFIG"), None, ctx.cwd):
            verdicts.append(cluster_write(f"{name} (it changes the kubeconfig's current context "
                                          "or namespace)"))
    elif name == "helm":
        verdicts.append(check_helm(words, env, ctx.cwd, piped_args="xargs" in marks))
    elif name == "kind":
        verdicts.append(check_kind(words, env, ctx.cwd))
    elif name in GENERIC_CLUSTER:
        verdicts.append(check_generic_cluster(name, words, env, ctx.cwd))
    elif name == "doctl":
        verdicts.append(check_doctl(words))
    elif name == "cloudflared":
        verdicts.append(check_cloudflared(words))
    elif name in {"terraform", "tofu"}:
        if any(w in TERRAFORM_WRITE for w in words[1:3]):
            verdicts.append(deny(f"{name} {words[1]} changes infrastructure; the owner runs it."))
    elif name == "git":
        verdicts.append(check_git(words, ctx, depth))
    elif name.startswith("git-") and len(name) > 4:
        verdicts.append(check_git(["git", name[4:], *words[1:]], ctx, depth))
    elif name in {"rm", "shred", "unlink"}:
        verdicts.append(check_rm(words, ctx))
    elif name == "mv":
        verdicts.append(check_mv(words, ctx))
    elif name == "find":
        verdicts.append(check_find(words, ctx))
        for block in find_exec_blocks(words):
            inner = Segment()
            inner.words = ["xargs", *block]  # {} is filled in by find, as xargs does
            verdicts.append(analyse_segment(inner, ctx, depth + 1))
    elif name in {"docker", "podman", "nerdctl"}:
        verdicts.append(check_docker(words, ctx.cwd))
    elif name == "crane":
        verdicts.append(check_crane(words))
    elif name in {"gradlew", "gradle"}:
        verdicts.append(check_gradle(words))
    elif name in INSTALLERS:
        verdicts.append(check_install(name, words))
    elif re.match(r"^python[0-9.]*$", name) and words[1:3] == ["-m", "pip"]:
        verdicts.append(check_install("pip", ["pip", *words[3:]]))
    elif name in {"glab", "gh"}:
        verdicts.append(check_gh(name, words))
    elif name not in NAMES_A_TOOL_SAFELY and any(
            CLUSTER_WORD.match(os.path.basename(w.split(" ", 1)[0])) for w in words[1:]):
        verdicts.append(ask(f"{name} runs or names a cluster tool the guard cannot check through "
                            f"{name}. Run the cluster command directly."))
    return worst(verdicts)


def analyse(command: str, ctx: Ctx, depth: int = 0) -> Verdict:
    if depth > 4:
        return deny("Command nests shells too deeply to check; split it into simpler commands.")
    if len(command) > MAX_COMMAND:
        return deny(f"Command is longer than {MAX_COMMAND} characters; split it into shorter commands.")
    try:
        toks, subs, bodies = tokenize(command)
    except ParseError as e:
        return deny(f"Guard could not parse this command ({e}); rewrite it with simpler quoting.")
    verdicts = [analyse(s, ctx, depth + 1) for s in subs]
    defines_function = any(toks[k][0] == "word" and toks[k + 1] == ("op", "(")
                           and toks[k + 2] == ("op", ")") for k in range(len(toks) - 2)) or any(
        toks[k] == ("word", "function") and (k == 0 or toks[k - 1][0] == "op")
        for k in range(len(toks)))
    if defines_function and CLUSTER_OR_PROTECTED.search(command):
        verdicts.append(ask("A shell function that names a cluster tool or a protected path hides "
                            "the real command from the guard. Write the command out."))
    for seg in segments(toks, bodies):
        verdicts.append(analyse_segment(seg, ctx, depth))
    if depth == 0:
        if ctx.decoder and (ctx.secret_words or ctx.names & (KUBE_NAMES | {"helm"})):
            verdicts.append(deny("Decoding Secret data prints secret values."))
        if ctx.piped_script:
            if ctx.names & {"curl", "wget"}:
                verdicts.append(deny("Piping a download into a shell installs unpinned code."))
            else:
                verdicts.append(ask("A script piped into a shell or interpreter cannot be checked. "
                                    "Run the commands directly."))
    return worst(verdicts)


# ----------------------------------------------------------------------------------------------
# Tool dispatch
# ----------------------------------------------------------------------------------------------

ID_RE = re.compile(r"(?<![0-9a-f])[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}"
                   r"(?![0-9a-f])")


def notion_strings(value: object) -> list[str]:
    """Every string in a Notion tool input except page text (NOTION_CONTENT_KEYS)."""
    found: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if str(k) not in NOTION_CONTENT_KEYS:
                found += notion_strings(v)
    elif isinstance(value, list):
        for v in value:
            found += notion_strings(v)
    elif isinstance(value, str):
        found.append(value)
    return found


def check_notion(tool: str, tool_input: dict) -> Verdict:
    action = tool.rsplit("__", 1)[-1]
    if action.startswith(NOTION_READ_PREFIXES):
        return OK
    strings = [s.lower() for s in notion_strings(tool_input)]
    flat = [s.replace("-", "") for s in strings]
    # A protected ID anywhere in a target string, with or without dashes, as an ID or inside a URL.
    hit = [n for pid, n in NOTION_PROTECTED.items() if any(pid in s for s in flat)]
    if hit:
        return deny(f"Notion {action} on {', '.join(sorted(set(hit)))}: the Notion database design "
                    "is read-only. Code follows Notion; report gaps to the owner as lead decisions.")
    targets = {m.replace("-", "") for s in strings for m in ID_RE.findall(s)}
    if targets and all(t in NOTION_EDITABLE for t in targets):
        return OK
    return ask(f"Notion {action} on a page that is not one of the editable pages "
               f"({', '.join(NOTION_EDITABLE.values())}).")


MCP_READ_ACTION = re.compile(r"^(notion-)?(search|fetch|get|list|query|read|download|show|find|"
                             r"describe|preview|introspect|analyze|lint|open|wait)", re.I)
MCP_PATH_KEY = re.compile(r"path|file|dir", re.I)
PATCH_PATH = re.compile(r"^(?:\*\*\* (?:Add|Update|Delete) File: |\*\*\* Move to: |\+\+\+ b/|--- a/"
                        r"|diff --git a/)(\S+)", re.M)


def mcp_paths(value: object, key: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            found += mcp_paths(v, str(k))
    elif isinstance(value, list):
        for v in value:
            found += mcp_paths(v, key)
    elif isinstance(value, str):
        if MCP_PATH_KEY.search(key) and key not in {"projectPath"}:
            found.append(value)
        if key in {"input", "patch"}:
            found += PATCH_PATH.findall(value)
    return found


def check_mcp(tool: str, ti: dict, ctx: Ctx) -> Verdict:
    """File paths in any other MCP tool (PyCharm read_file, create_new_file, apply_patch, ...)."""
    action = tool.rsplit("__", 1)[-1]
    repo = ctx.repo()
    verdicts = []
    for raw in mcp_paths(ti):
        raw = raw.removeprefix("file://")
        p = resolve(raw, Path(str(ti.get("projectPath") or ctx.cwd)))
        if is_secret_path(p):
            verdicts.append(deny(f"{raw} {SECRET_DENY}"))
        if claude_config(p, repo) and not MCP_READ_ACTION.match(action):
            verdicts.append(ask("This changes the Claude guard configuration; the owner approves."))
        if constitution(p, repo) and not MCP_READ_ACTION.match(action):
            verdicts.append(ask(CONSTITUTION_ASK))
    text = "".join(str(ti.get(k, "")) for k in ("text", "input", "patch", "content"))
    if text and not MCP_READ_ACTION.match(action) and SECRET_CONTENT_RE.search(text):
        verdicts.append(deny("The new text holds a private key or credential."))
    return worst(verdicts)


def check_file_tool(tool: str, ti: dict, ctx: Ctx) -> Verdict:
    raw = ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or ""
    verdicts = []
    if raw:
        p = resolve(str(raw), ctx.cwd)
        if is_secret_path(p):
            verdicts.append(deny(f"{p} is a secret file or folder. Never read, print or copy it."))
        repo = ctx.repo()
        if tool in {"Edit", "Write", "MultiEdit", "NotebookEdit"} and claude_config(p, repo):
            verdicts.append(ask("This changes the Claude guard configuration; the owner approves."))
        if tool in {"Edit", "Write", "MultiEdit", "NotebookEdit"} and constitution(p, repo):
            verdicts.append(ask(CONSTITUTION_ASK))
        if tool in {"Edit", "Write", "MultiEdit", "NotebookEdit"} and is_rc(p):
            verdicts.append(ask(RC_ASK))
    glob_pat = str(ti.get("glob") or "")
    if tool == "Glob":
        glob_pat += " " + str(ti.get("pattern") or "")
    if re.search(r"\.env\b(?!\.example)|\.(pem|key)\b|kubeconfig\b", glob_pat):
        verdicts.append(deny("This search targets secret files."))
    if tool in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        edits = ti.get("edits") if isinstance(ti.get("edits"), list) else []
        text = str(ti.get("content", "")) + str(ti.get("new_string", "")) + str(
            ti.get("new_source", "")) + "".join(
            str(e.get("new_string", "")) for e in edits if isinstance(e, dict))
        if SECRET_CONTENT_RE.search(text):
            verdicts.append(deny("The new text holds a private key or credential. Never write "
                                 "secret values into files."))
    return worst(verdicts)


SHELL_TOOLS = {"Bash", "Monitor"}
FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Grep", "Glob"}


def is_shell_tool(tool: str) -> bool:
    return tool in SHELL_TOOLS or tool.endswith("__execute_terminal_command")


def decide(event: dict) -> Verdict:
    if not isinstance(event, dict):
        raise TypeError("event is not a JSON object")
    tool = event.get("tool_name")
    if not isinstance(tool, str):
        raise TypeError("tool_name is missing")
    ti = event.get("tool_input")
    if ti is None:
        ti = {}
    if not isinstance(ti, dict):
        raise TypeError("tool_input is not an object")
    cwd = Path(str(event.get("cwd") or os.getcwd()))
    ctx = Ctx(cwd)
    if is_shell_tool(tool):
        command = ti.get("command")
        if tool == "Monitor" and command is None:
            return OK  # a WebSocket monitor runs no command
        if not isinstance(command, str):
            raise TypeError("command is not a string")
        if "\0" in command:
            return deny("The command holds a NUL byte; the guard cannot check it.")
        ctx.raw = command
        return analyse(command, ctx)
    if tool in FILE_TOOLS:
        return check_file_tool(tool, ti, ctx)
    if tool.startswith("mcp__") and re.search(r"notion", tool, re.I):
        return check_notion(tool, ti)
    if tool.startswith("mcp__"):
        return check_mcp(tool, ti, ctx)
    return OK


def failure_verdict(tool: object, why: str) -> Verdict:
    """What a guard error or timeout means: block anything that runs or writes, ask for reads."""
    msg = f"Guard error ({why}). The call was not checked"
    name = tool if isinstance(tool, str) else ""
    if name in {"Read", "Grep", "Glob", "Edit", "Write", "MultiEdit", "NotebookEdit"} or (
            name.startswith("mcp__") and not is_shell_tool(name)
            and MCP_READ_ACTION.match(name.rsplit("__", 1)[-1])):
        return ask(msg + "; please confirm it.")
    return deny(msg + ", so it is blocked. Retry with a simpler command, or ask the owner.")


def _on_alarm(signum: int, frame: object) -> None:
    raise GuardTimeout(f"no decision within {BUDGET_SECONDS} s")


def main() -> int:
    tool: object = None
    try:
        if hasattr(signal, "SIGALRM"):
            signal.signal(signal.SIGALRM, _on_alarm)
            signal.alarm(BUDGET_SECONDS)
        event = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
        tool = event.get("tool_name") if isinstance(event, dict) else None
        verdict = decide(event)
    except BaseException as e:  # noqa: BLE001 - a guard bug or timeout must never silently allow
        verdict = failure_verdict(tool, f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
    if verdict.decision:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": verdict.decision,
            "permissionDecisionReason": "Guard: " + verdict.reason,
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
