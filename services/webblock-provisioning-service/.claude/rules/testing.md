---
paths:
  - "tests/**"
---

# Test rules (owner rules)

How to run and report the suites: the `provisioning-gates` skill. CLAUDE.md section 4 gives the short form.

- **Tests first.** Write the failing test from the FR acceptance rows (`.claude/docs/requirements/FRD.md`), see it
  fail, then write the code.
- **The FR ID is in the test.** In its name (`test_fr12_...`) or its docstring (`"""FR-12: ..."""`). A purely
  technical test names its TR ID. The direction check traces requirements to tests by these IDs.
- **No skipped tests.** A dependency suite fails, never skips, when its server is missing (`tests/conftest.py`).
  No `skip`, `xfail` or `-k` filter to make a gate pass. Collecting no test fails.
- **Fakes, not mocks of our own code.** Use the in-repo fakes and real adapters (Testcontainers PostgreSQL 18 and
  RabbitMQ). Do not mock a class of this package to repeat what the code does; mock only an outside boundary.
- **Test behaviour and failure paths.** Every test asserts something. Cover the negative cases, conflicts, crashes
  and retries the FR rows name, not only the happy path.
- **Layout.** `tests/unit/` mirrors the package and imports only public names. No `__init__.py` under `tests/`;
  each folder gets its pytest marker from `tests/conftest.py`. Any warning is an error.
- **Real services only from the suite.** Testcontainers suites use only containers they start, never the owner's.
  Kind suites run only on the project's Kind cluster (CLAUDE.md section 0), started by `kind-runner`.
- **Migrations.** Every schema change ships with a test in `tests/integration/` (upgrade tests with old rows).
