---
paths:
  - "src/test/**"
---

# Test rules (owner rules)

How to run and report the suites: the `project-gates` skill. CLAUDE.md section 4 gives the short form.

- **Tests first.** Write the failing test from the FR acceptance rows (`.claude/docs/requirements/FRD.md`), see it
  fail, then write the code.
- **The FR ID is in the test.** `@DisplayName("FR-12: owner renames the project")` on the method, or the method name
  (`fr12_ownerRenamesTheProject`). A purely technical test names its TR ID. The direction check traces requirements
  to tests by these IDs. Tests older than the FRD carry no ID; add one when you change such a test.
- **No skipped tests.** No `@Disabled`, no `Assumptions`, no `--tests` filter or excluded tag to make a gate pass.
  Testcontainers suites fail, never skip, when Docker is missing.
- **Which task runs it.** Untagged tests (unit, `@WebMvcTest` slices, ArchUnit) run in `test`.
  `@Tag("integration")` and `@Tag("messaging")` run in `integrationTest`. Use the meta-annotations in `support/`:
  `@ServiceIntegrationTest` (whole app, prototype profile, both databases and RabbitMQ) and `@JpaSliceTest` (JPA on
  real PostgreSQL, no test transaction, so commits and rollbacks are real).
- **Real services, not fakes of them.** PostgreSQL 18 (`postgres:18`) and RabbitMQ (`rabbitmq:4.3.6-management-alpine`)
  through Testcontainers (`support/ServiceTestDatabases`, `db/PostgresFixture`). Suites use only containers they
  start, never the owner's.
- **Mock only at a boundary.** `@MockitoBean` for the service under a controller in a web slice (import
  `WebSliceSupport.Imports`), or for an outside system such as the JWT decoder. Payment HTTP goes through WireMock.
  Never mock a class of this service to repeat what its code does.
- **Time and waiting.** Move time with `MutableClock` and `TestClockConfig`; wait for async work with Awaitility, not
  a fixed sleep.
- **Test behaviour and failure paths.** Every test asserts something. Cover the refusals, conflicts (stale
  `edit_revision`, reused idempotency key), not-the-owner 404s and retries the FR rows name.
- **Schema tests.** Every migration ships with a test in `src/test/java/org/kshrd/webblock/project/db/` (roles,
  grants, constraints, upgrade with old rows).
- **ArchUnit rules are tested too.** A new or changed rule in `architecture/` ships with a test that proves it
  catches a planted violation (classes in `architecture/fixture/`), as `MethodSecurityRuleTest` does.
- **Layout.** `src/test/java` mirrors the main packages; shared helpers live in `support/`.
