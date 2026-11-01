# WebBlock Claude Code setup

Adds `CLAUDE.md` + `.claude/` to the repo root and to each of the 9 services, plus shared docs, contracts rules and tooling.
It adds **no source code and no build files**.

## Getting started (teammates)
1. **Install the tools:** [Claude Code](https://claude.com/claude-code), `git`, `jq`, and `python3` (or `python`). On Windows use Git Bash or WSL, because the hooks run in bash.
2. **Check the setup:** from the repo root run `bash tooling/sync-claude.sh --check`. It should print `OK`.
3. **Open your service:** `cd services/<your-service> && claude`. Always start Claude inside your own service folder so that service's rules, skills and permissions apply. They are not inherited from the root.
4. **Find your service:** open its `CLAUDE.md`. It lists the commands, layout and Don'ts, and any `TODO(<owner>)` line is for you to fill in.
5. **Try the flow on a small task:** `/plan <task>`, approve it, `/implement`, `/test`, then `/review` and `/security-review`. For a bigger feature with requirements, tests first and review gates, use `/spec <feature>`.

Good to know:
- Claude starts in plan mode and cannot edit until you approve the plan.
- Contract-first: an API or event change goes in `contracts/` before any code.
- One service per task. If you need a change in another service, Claude writes a note for its owner instead.
- Never put secrets in the repo. Claude cannot read `.env*` or `application-prod*` files.
- Claude cannot edit hooks, `settings*.json`, `.mcp.json`, CI or CODEOWNERS. Ask the tech lead to change those.
- If a hook blocks something it should not, tell the tech lead. Do not bypass it.

## Install (in your repo)
1. `git checkout -b chore/claude-setup`
2. Copy everything in this folder into the repo root. Do NOT overwrite your existing `.mcp.json` (use `.mcp.json.example` as a guide) and merge `gitignore.append` into your `.gitignore`.
3. If you already have a root `CLAUDE.md` or a `docs/` folder, merge instead of replacing.
4. Install the hook prerequisites: `jq`, `git`, `python3` (required); `gitleaks`, `ruff` (optional). The hooks run in bash: use macOS, Linux, WSL or Git Bash on Windows.
5. `chmod +x tooling/sync-claude.sh .claude/hooks/*.sh services/*/.claude/hooks/*.sh tooling/claude-shared/hooks/*.sh`
6. Run `tooling/sync-claude.sh --check` (should print `OK`).
7. Replace every `TODO(...)` with real facts (owners, packages, package manager, ports) and the `@TODO-...` handles in `.github/CODEOWNERS`.
8. Commit on the branch and open one PR; let each service owner review their service folder.

## How to use it
- Work on one service: `cd services/<service> && claude`. That folder's `CLAUDE.md`, rules, skills, agents, hooks and `settings.json` apply, and the root `CLAUDE.md` is loaded too.
- Cross-service work (contracts, docs, tooling): start Claude at the repo root.
- Flow: `/plan <task>` -> approve -> `/implement` -> `/test` -> `/review` + `/security-review` -> `/commit` -> `/pr`.
- Bugs: `/fix-bug <error or description>` (evidence first).
- Check what is active: `/permissions`, `/hooks`, `/agents`, `/memory`.

## Layout
```
CLAUDE.md  .mcp.json.example  gitignore.append  .gitleaks.toml  .github/CODEOWNERS
.claude/                 root: settings, rules, skills, commands, agents, hooks
docs/                    architecture, service-map, auth-flow, security-model, mcp-and-permissions, decisions/
contracts/               rules + changelog (OpenAPI + AsyncAPI/JSON Schema go here)
tooling/                 sync-claude.sh and claude-shared/ (MASTER copies)
services/<service>/      CLAUDE.md + .claude/  (rules, skills, commands, agents, hooks, settings.json)
```

## Keeping services in sync
Shared files (security rules, hooks, commands, Spring/Python rule packs, settings) are copies of `tooling/claude-shared/`.
- Edit the master in `tooling/claude-shared/`, then run `tooling/sync-claude.sh`.
- CI: run `tooling/sync-claude.sh --check`; it fails when a copy has drifted.
- Service-specific files (e.g. `keycloak.md`, `payment-safety.md`, `infra-safety.md`) are never overwritten.

## Security notes
- Hooks and permission rules are guardrails, not a sandbox. Use a devcontainer/VM without production credentials for stronger isolation, and keep CI checks (gitleaks, ArchUnit, Spectral, `flyway validate`, dependency scan, CODEOWNERS) as the real enforcement.
- `settings.json` is not inherited from parent folders; each service has its own full copy.
- Claude cannot edit `.claude/settings*.json`, `.claude/hooks/`, `.mcp.json`, CI workflows or CODEOWNERS (hook `protect-files.sh`): a human changes those.

## Verify against your Claude Code version
Syntax was checked against the official docs (permissions rule syntax, hook exit codes, `paths:` rule frontmatter, nested skills/rules/agents). Two things are not documented and should be tested once: whether nested `.claude/commands` are discovered (commands are copied into every service anyway, so launching inside a service works), and the exact JSON `ask` decision returned by `protect-contracts.sh`.
