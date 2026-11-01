# Security review: hooks on Windows (guard.py, reviewer_writes.py, post_edit_check.py)

Stage 5 (Verify). Branch `hooks-windows-2`, range `ba1b643..7655db1` (one commit, 7655db1). Reviewer model: Claude
Opus 5.5 (`claude-opus-5-5`). In scope: TR-13 (parent: all FR; the guard hook). Articles checked: Art. VI (hooks
are never worked around, no secrets, settings and constitution edits ask the owner) and Art. VIII (reviewers write
only their reports). Art. II and III are not touched (no dependency, no service code).

This file was meant for `ai/reviews/` in the worktree. The live reviewer write limit in the main checkout only
allows `ai/reviews/` of the main checkout, so it was written here instead. Move it if needed. It is not committed.

## Method

Skills: `differential-review` (diff, blast radius, fail-closed paths, untested changes) and `sharp-edges` (path
canonicalisation and error paths as footguns). I read every helper the diff adds or changes in full: `native`,
`is_abs_word`, `resolve`, `pkey`, `rel_key`, `name_key`, `last_part`, `cmd_name`, `inside`, `is_top`, `repo_rel`,
`scratch_root`, `iglob_all`, `_iglob_hidden`, `path_list`, `mount_sources`, `claude_config`, `constitution`,
`rm_one`, `check_mv`, `spec_names`, `check_git_add`, `in_project` and the `reviewer_writes` call site.

The probes sent hook events (JSON on stdin) to the guard. `USERPROFILE` and `HOME` pointed at `D:\fakehome`, which
does not exist, so no real home folder or kubeconfig was opened. The base guard and the new guard ran side by side
on the same events. The base source came from `git show ba1b643:.claude/hooks/guard.py` and ran from memory. Node
(the runtime of the Claude Code file tools) and Git Bash were asked only whether a spelling opens a tracked file that
holds no secret (`.claude/settings.json`, `CONSTITUTION.md`, `build.gradle`). Those checks only read.

| Command | Result | Notes |
| --- | --- | --- |
| `git diff ba1b643..7655db1` (5 files) | read in full | 363 insertions, 61 deletions |
| `python3 -I .claude/hooks/test_guard.py` (Windows, Python 3.14.6) | passed 749 of 749 | 1 skipped (slow git) |
| `python -I .claude/hooks/test_guard.py` (Windows, Python 3.10.11, glob fallback) | passed 749 of 749 | 1 skipped (slow git) |
| `python3 -I .claude/hooks/test_hooks.py` (Windows, Python 3.14.6) | passed 230 of 230 | |
| `python -I .claude/hooks/test_hooks.py` (Windows, Python 3.10.11) | passed 230 of 230 | |
| Guard probes, new guard (Windows): 61 events | read | see findings |
| Guard probes, base and new (Windows): 26 events | read | in 7 cases the base blocks and the new guard allows |
| Reviewer limit probes (Windows): 10 events as `security-reviewer` | read | aliases fail closed |
| `iglob_all` and `_iglob_hidden` on 10 patterns in the worktree (Python 3.14) | same result on all 10 | the fallback matches glob |
| Node `fs.readFileSync` on 11 spellings of tracked files | read | `\\?\`, `\\.\`, `\\localhost\D$` and `::$DATA` open the file; a trailing dot or space does not |
| Git Bash `test -d` on `\\?\D:\...`, `\\.\D:\...`, `//localhost/D$/...`, and `cat` on `/proc/cygdrive/d/...` | all open | MSYS programs accept every spelling |

Not run, and why:

- The Linux runs in Docker: the new suites, the base `test_guard.py` against the new guard, and the base against
  the base. The reviewer write limit (`reviewer_writes.py`) denies `docker run` and denies any scratch file outside
  `ai/reviews/`, so no container could start. Instead I compared the Linux decision logic helper by helper (see
  "Checked and clean"). The Linux counts (694 of 694 and 215 of 215) are the implementer's; this review did not
  re-run them. Status: not run.
- 8.3 short names on drive C: (the home folder) were not probed, to keep away from `~/.kube`. Drive D: has no short
  names (`GetShortPathNameW` returns the long path for the worktree), so this machine has no 8.3 alias of the
  repository.
- `scripts/osv-scan.sh`: no dependency change. The `./gradlew` gates: no Java change.

## Findings

### H1 (high): a repository path spelled `\\?\`, `\\.\` or as a UNC share now writes guard files with no ask

- Where: `.claude/hooks/guard.py:662-670`. `repo_rel` returns None when the drive strings differ. `claude_config`
  (`:2167`), `constitution` (`:2182`), `check_file_tool` (`:2741`), `check_mcp` (`:2721`) and the shell write check
  all use it. This breaks TR-13 and Art. VI: "the hooks are never worked around ... edits to this page, the hooks and
  `settings.json` ask the owner". It also breaks point 3 of the brief: the guard no longer fails closed here.
- What changed: on Windows, `os.path.splitdrive` gives `\\?\D:`, `\\.\D:` or `\\localhost\D$` for these spellings.
  None of them equals the repository's `D:`. So `repo_rel` answers "another drive, outside the repository", and the
  guard allows the write. For the same input in the base, `os.path.relpath` raised `ValueError` and the guard failed
  closed: file tools were asked, Bash and MCP were denied. Node and Git Bash open all three spellings as the real file
  (checked read only on `.claude/settings.json`).
- Measured:

  | Event | base | new |
  | --- | --- | --- |
  | `Write \\?\<repo>\.claude\hooks\guard.py` | ask | allow |
  | `Write \\.\<repo>\.claude\hooks\guard.py` | not run | allow |
  | `Write \\localhost\D$\<repo>\.claude\settings.json` | ask | allow |
  | `Edit \\localhost\D$\<repo>\.claude\docs\CONSTITUTION.md` | ask | allow |
  | `NotebookEdit \\?\<repo>\.claude\docs\CONSTITUTION.md` | not run | allow |
  | `mcp__pycharm__create_new_file`, `pathInProject` `\\?\<repo>\.claude\settings.json` | deny | allow |
  | Bash `echo x > '\\?\<repo>\.claude\settings.json'` | deny | allow |
  | Bash `cp x '\\?\<repo>\.claude\hooks\guard.py'` | deny | allow |

- How it could be abused: one call, no setup. An agent that is refused an edit of `guard.py` sends the same edit with
  the `\\?\` prefix. Then the guard, and the owner's ask, are gone for every later call. The commit's own Windows
  cases (`test_guard.py` `windows_cases`) test only another drive letter, so no test covers this.
- Suggested fix: in `resolve` (or `pkey`), map `\\?\X:\` and `\\.\X:\` to `X:\`, and map `\\?\UNC\h\s\` to
  `\\h\s\`. Then let `repo_rel` return None only when both sides are plain drive letters (`X:`) that differ. For any
  UNC or device root it should raise, so the guard fails closed and a write there asks. The text alone cannot show
  whether a share (`\\localhost\D$`, `\\host\share`) points at the repository, so asking for writes to any UNC path is
  the safe answer. Add the eight rows above to `windows_cases`: ASK for file tools, DENY or ASK for Bash and MCP.

### M1 (medium): Windows path aliases skip the secret and protected-file checks (not new, still open)

- Where: `.claude/hooks/guard.py:613` (`pkey`), `:624` (`name_key`), `:643` (`is_secret_path`) and `:576`
  (`native`). This breaks Art. VI: never read a secret or a kubeconfig, and edits of `settings.json` and the
  constitution ask. Each row gives the same answer in the base and the new guard, so it is not a regression. The
  change sets out to make the Windows rules hold, and these are the spellings it does not cover.
- Measured on the new guard (all `allow`):
  - `Read \\?\<home>\.kube\config`. Node opens `\\?\` paths, so this reads the kubeconfig.
  - `Read <repo>\.env::$DATA`, `Read <home>\.netrc::$DATA` and `Read <repo>\id_rsa::$DATA`. In Node, `::$DATA` gives
    the file's own content (checked on `build.gradle::$DATA`).
  - `Write <repo>\.claude\settings.json::$DATA` and `Edit <repo>\.claude\docs\CONSTITUTION.md::$DATA`. Node opened
    both files through this spelling (read only).
  - Bash `cat /proc/cygdrive/d/fakehome/.kube/config`, `cp x /proc/cygdrive/d/<repo>/.claude/hooks/guard.py` and
    `docker run -v /proc/cygdrive/d/fakehome/.kube:/k img`. `native` maps `/c/...` but not `/proc/cygdrive/c/...`,
    and Git Bash opens the second form too (checked with `cat` on `settings.json`).
  - Bash `findstr . '.env::$DATA'`. The `$` makes the guard treat the word as a variable, so it matches only the
    literal name, and `.env::$DATA` is not a secret name.
- Not exploitable here: a trailing dot or space on a folder (`.kube.\config`, `.claude \settings.json`). The guard
  allows `Read <home>\.kube.\config`, but Node and Git Bash both answer ENOENT, so nothing is read.
- How it could be abused: each needs one call. The worst is the `\\?\` read of `~/.kube/config` (Art. VI and
  CLAUDE.md section 0).
- Suggested fix, in the same code as H1: in `native` or `resolve`, also map `/proc/cygdrive/x/` to `X:/`. Strip a
  stream suffix (`:name`, `::$DATA`, any `:` after the drive) from each part before `pkey`, `name_key` and the
  `claude_config` and `constitution` match, or ask for any path with a `:` after the drive. Fix it in the same round
  as H1.

### M2 (medium): `cmd`, `powershell`, `pwsh`, `wsl` and `start` run commands the guard does not read (not new)

- Where: `.claude/hooks/guard.py:2289` (`SHELLS` lists only the POSIX shells) and the shell branch of
  `analyse_segment`. On Windows this breaks TR-13 and Art. VI (no push; the hooks are never worked around).
- Measured on the new guard (all `allow`): `cmd //c "git push origin main"`, `cmd.exe /c git push`,
  `'C:\Windows\System32\cmd.exe' /c git push`, `powershell -c "git push origin main"`, `pwsh -Command git push`,
  `wsl git push`, `start git push`, `cmd //c "rmdir /s /q src"`, `cmd //c mklink /J build\j '<repo>'` and
  `subst X: '<repo>'`. The base also allowed `cmd.exe /c git push` and `powershell -c ...`. It denied
  `cmd //c ...` only because it crashed on the `//c` word (`ValueError`, so it failed closed). The new code removes
  that accidental block.
- The guard's own docstring lists "an interpreter one-liner" as a known limit, and on Linux it allows
  `python3 -c "import os; os.system('git push')"` as well. So this is a known limit, not a regression. However, the
  guard does unwrap and check `bash -c`, `sh -c` and now `bash.exe -c`. On Windows, `cmd /c` and `powershell -c` are
  the everyday shells and differ from those by one word.
- Suggested fix: treat `cmd` (`/c`, `/k`), `powershell` and `pwsh` (`-c`, `-Command`; deny or ask for
  `-EncodedCommand`), `wsl` and `start` as wrappers. Where the rest of the command is plain, analyse it as a command;
  otherwise ask. Ask for `subst` and `mklink`. If this round does not fix it, record it as a TR-13 backlog item.

### L1 (low): a drive letter that points at the repository (`subst`, a mapped drive) or a junction counts as outside it

- Where: `.claude/hooks/guard.py:662` (`repo_rel`) and `:652` (`inside`). After `subst X: <repo>` (allowed, see M2),
  the guard allows `Write X:\.claude\hooks\guard.py`. It denies `rm -rf X:\src`, but only as "outside the
  repository". A junction made with `cmd //c mklink /J` works like a Linux symlink, and the guard does not follow
  symlinks on Linux either (`resolve` uses `normpath`, not `realpath`). The attack takes two steps and belongs to
  the existing symlink limit. Record it as a known limit, or compare `os.path.realpath` paths in `repo_rel` and
  `inside`; for paths that exist, `realpath` resolves `subst` drives and junctions.

### L2 (low): the scratch root follows `tempfile.gettempdir()`

- Where: `.claude/hooks/guard.py:684`. Python reads `TMPDIR` before `TEMP`, but Claude Code (Node `os.tmpdir()`)
  reads `TEMP` and `TMP`. If no candidate folder is writable, Python falls back to the current folder. Then
  `<repo>\claude` would be a folder that `rm -rf` may delete. Both cases need a changed environment, and the root
  always ends in `claude`. Every probe of the boundary failed closed: `claude.\x`, `..\x`, `\\?\...\claude\x`,
  `/tmp/x` and `/tmp` are all denied. `CLAUDE\x` is allowed, which is correct because it is the same folder.
  Suggested fix: build the root from `os.environ["TEMP"]`, as Node does, and refuse a root inside the repository.

## Checked and clean

- Linux and macOS decisions (point 1 of the brief): when `WINDOWS` is false, every new helper returns exactly what
  the base code computed.
  - `native` returns the word unchanged, and `is_abs_word` is `startswith("/")`.
  - `pkey` is `str(p)`, so `inside` is byte for byte the base. `rel_key` and `name_key` return their input.
  - `last_part` equals `w.rstrip("/").rsplit("/", 1)[-1]`, and `cmd_name` is `os.path.basename`.
  - `is_top` is the base tuple test. `repo_rel` is `os.path.relpath` and never returns None. `path_list` is
    `split(":")`.
  - The `mount_sources` and `looks_like_path` additions run only on Windows.
  - `is_secret_path` now compares `str` values instead of `Path` values. The paths are already normalised, so the
    result is the same.
  - On Python 3.11 and later, `iglob_all` is `glob.iglob(..., include_hidden=True)`.
  So no Linux decision can move from deny or ask to allow. This was checked by reading only; the Linux suites were
  not run (see Method).
- `cmd_name` (lower case, `.exe`, `.com`, `.bat` and `.cmd` stripped) can only add matches on Windows. `git.exe push`,
  `GIT.EXE push`, `bash.exe -c 'git push'` and `C:\...\git.exe push` now deny; the base allowed the first two
  (measured). In `reviewer_writes.py:133` it can only move a name into a list that is denied or checked:
  `GIT.EXE commit` and `gradlew.bat spotlessApply` deny.
- Fail closed (point 3 of the brief): `main` still catches `BaseException` and answers deny or ask, and a deep
  recursion in `_iglob_hidden` ends there. The only new "outside" answer is `repo_rel` returning None (H1). `rm_one`
  and `check_mv` still treat "outside the repository" as deny (recursive) or ask: `rm '\\?\<repo>\docs\notes\...'`
  asks, and `rm -rf /proc/cygdrive/.../src` denies.
- Scratch root (point 2 of the brief): in Git Bash, `/tmp` is the `usertemp` mount (`/etc/fstab`), and
  `cygpath -w /tmp` gives `C:\Users\Zeven\AppData\Local\Temp`. So `native` maps `/tmp` to that same folder. The root
  is `%TEMP%\claude`, the parent of every Claude Code session scratchpad, which matches `/tmp/claude-<uid>` on Linux.
  Escapes after the root are normalised: `/tmp/claude/../x` denies.
- Secrets in the plain Windows spellings deny: `cat /d/fakehome/.kube/config`, `Read D:\fakehome\.kube\config`,
  `docker run -v /d/fakehome/.kube:/k img`, `cat .ENV` and `cat '.env.'`. The base allowed the first three.
- `KUBECONFIG` lists with `;` and drive letters: a list that holds a kubeconfig other than the default asks
  (measured).
- Reviewer write limit (point 5 of the brief): `in_reviews` is an allow list checked after `realpath`, so every alias
  fails closed. `Write \\?\<repo>\src\A.java`, `Write <repo>\src\A.java::$DATA`,
  `Write //localhost/D$/<repo>/src/A.java` and Bash `echo x > '\\?\<repo>\src\A.java'` all deny, and
  `Write <repo>\ai\reviews\x.md` is allowed. The guard allows `cmd //c "echo x > src\A.java"` and
  `powershell -c "Set-Content ..."`, which is the documented `python -c` limit (covered by M2).
  `post_edit_check.py:44` only decides whether to print style notes (exit 0, no decision), so it cannot let a write
  through.
- Test changes (point 4 of the brief):
  - `cd ~/.kube && cat config` moved to `home_cases`, with `HOME` and `USERPROFILE` set to a folder the test owns,
    which holds a dummy `.kube/config`. It still expects DENY and no longer depends on the machine.
  - The docker climb `up = max(4, len(repo.parts))` gives four `..` on Linux (`/tmp/<tmp>/repo` has four parts),
    the same as the base, and on Windows it climbs far enough to reach the drive root.
  - The new ALLOW case `cd $X && rm -rf <scratch>/x` also holds under the base logic, because an absolute target does
    not need a known working folder.
  - The slow-git case is skipped only on Windows, and the reason is printed. The Windows-only cases are skipped on
    Linux and counted.
- The diff holds no secret value, token or credential, and changes no dependency, settings file or constitution.

Verdict: needs fixes. H1 is a regression in failing closed on Windows: where the base asked or denied, the new guard
allows edits of `guard.py`, `settings.json` and the constitution. It blocks the merge until fixed. M1 and M2 are not
regressions. Fix them in the same round (the same code as H1), or record them as TR-13 backlog items. Reading the
code shows no change in Linux behaviour, but this review did not re-run the Linux suites.
