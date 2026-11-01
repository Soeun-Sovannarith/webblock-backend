# Validation: layer-first 5e5dffa..5f73286

Stage 5, the one review of the lean flow. IDs: TR-5, FR-4, TR-4, TR-2, TR-24. Commits: 2807b53 (code), 76f9548
(docs), 5f73286 (constitution version 7, docs only). The lead saved this report from the reviewer's hand-back: the
reviewer hook refused the write inside a worktree.

## Verdict: approved

- The code is an exact package move: every moved main file differs from 5e5dffa only in package and import lines and
  comment words ("module" became "feature"); moved tests differ only in package and imports.
- Gates (reviewer's own run): `./gradlew check --rerun-tasks` passed; `test` 854 (848 - 1 ModuleStructureTest + 5
  LayoutRuleTest + 2 GrpcServerTest), `integrationTest` 301, 0 failures, 0 errors, 0 skipped; coverage matches the
  same 8 Service and 5 Entity classes; spec-check 0 errors; contracts-sync --check clean at 18dd301. OSV not run (no
  dependency change).
- `GrpcServer.requireServicePackage` accepts exactly `<base>.presentation.grpc.<one segment>` or
  `<base>.common.presentation.grpc`: tighter than before everywhere outside the new feature folders.
- ArchUnit: nothing weaker beyond the two removed Modulith checks (feature isolation, module cycles), as ruled; the
  layout rules cannot pass vacuously.
- String-based names: none missed; `loader.main` and every doc name the new `Migrations` class.

## Findings

Important (owner action, not code): Notion "Web Block architecture" 6.1 still says "Project already uses these four
layers, grouped by feature first ... It keeps that layout; new services use the one above." Decision 0016 and Art. III
version 7 do not record that the owner's ruling overrides it, nor the folder choices beyond 6.1 (feature folders under
`presentation/messaging` and the `infrastructure` type folders, only `domain/<f>/model`).

Minor:

- `.claude/docs/decisions/0009-code-quality-tools.md:30` gives Modulith's `verify()` as the reason for no cycle rule.
- `.claude/docs/requirements/DIRECTION.md:164` gives "Modulith" as evidence that TR-2 is done.
- `.claude/CLAUDE.md:97` lists `presentation/messaging/<feature>/`, which has no class yet.
- `build.gradle:118`: `spring-modulith-starter-test` is used by no test.
