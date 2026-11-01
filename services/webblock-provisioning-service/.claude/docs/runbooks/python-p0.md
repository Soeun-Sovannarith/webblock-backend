# Python port, phase P0: tools, environment and gates

Decision `0013` (sections 4, 9 and 10) moves the Provisioning service to Python. This runbook covers
what task P0.1 delivered: the pinned tools, the virtual environment, and the `py-*` Make targets that
every later task runs. The Go reference and its targets are no longer on this branch (decision
`0014` section 18); see "The Go reference" below.

Nothing here touches a cluster, starts a container or reads `.env`.

## Requirements

- Linux x86_64 (the only platform the tool pins cover).
- The system Python 3.14 as `python3.14` on `PATH`. Every target checks it first and stops with exit
  status 2 if it is missing or reports another minor version. uv never downloads an interpreter.
- `curl`, `sha256sum`, `tar`, `awk` and `mktemp` for `make py-tools`; network access to
  `github.com` and `pypi.org` the first time.
- For the supply-chain gates (task H1): network access to `osv-vulnerabilities.storage.googleapis.com`
  (`py-vuln`), `pypi.org` and `api.github.com` (`py-age`), `grype.anchore.io` and Docker Hub
  (`image-scan`), on every run; Docker for `image-scan`.

## Install the tools

```sh
make py-tools       # uv, osv-scanner, goose, grype and protoc into ./bin
```

`scripts/py/tools.sh` installs five release binaries pinned in `scripts/py/versions.sh`: uv 0.12.18,
osv-scanner v2.6.0, goose v3.28.0, grype v0.119.0 (task H1) and protoc 36.2 (decision `0022` E3; it matches
the protobuf runtime and generates the gRPC message code). Each download must match both the
SHA-256 pinned there and the checksum file the project publishes (uv: `<asset>.sha256` and
`sha256.sum`; osv-scanner: `osv-scanner_SHA256SUMS`; goose: `checksums.txt`; grype:
`grype_<version>_checksums.txt`, and the binary inside the archive against its own pin). A mismatch stops the script
with exit status 3
and installs nothing. A binary already present with the pinned checksum is not downloaded again; one
with any other checksum is replaced. Downloads are HTTPS only, retried at most three times, each
with its own time limit, and the whole run stops after 900 seconds. Nothing is written outside
`./bin`. `bash scripts/py/tools.sh goose` installs one tool only.

Version rule (ruling 10.5): the newest release published on or before the `exclude-newer` date of
`pyproject.toml`, except goose, which stays at the v3.28.0 the Go reference used (branch `go-reference`).

## Where things live

| What | Where | Notes |
| --- | --- | --- |
| Virtual environment | `.venv/` at the module root | git-ignored; one per checkout or worktree, never under `bin/` (ruling 10.4) |
| uv cache | `bin/uv-cache/` | shared by worktrees through their `bin` link |
| Tool caches | `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/` | git-ignored |
| Coverage working files | `.coverage-run.*/` | git-ignored; `py-cover` removes them |
| Coverage recordings of the separately run suites | `.coverage-data/` | git-ignored; written by a suite run with `PY_COVER=1`, read by `py-cover` |
| Advisory and scanner databases | `bin/.py-vuln.*/`, `bin/.image-scan.*/` | one fresh download per run, removed at the end |
| Age waivers, image-scan exceptions | `scripts/py/age-waivers.toml`, `scripts/py/image-scan-exceptions.toml` | committed; reviewed changes |
| Lock | `uv.lock` | committed; changed only by `uv lock` |

Nothing is written under `$HOME`.

## uv settings

Every target runs uv with `UV_PROJECT_ENVIRONMENT=$(CURDIR)/.venv`,
`UV_CACHE_DIR=$(CURDIR)/bin/uv-cache`, `UV_PYTHON_DOWNLOADS=never`,
`UV_PYTHON_PREFERENCE=only-system`, `UV_PYTHON=python3.14` and `UV_NO_CONFIG=1` (ruling 10.4).

`UV_NO_CONFIG=1` makes uv ignore every configuration file, **including the `[tool.uv]` table of
`pyproject.toml`** (checked with uv 0.12.18: without it, `uv lock --check` reports "Resolving
despite existing lockfile due to removal of global exclude newer" and fails). The targets therefore
pass the two project settings explicitly: `UV_EXCLUDE_NEWER`, read from `[tool.uv] exclude-newer`
in `pyproject.toml` (the only place it is written), and `UV_NO_BUILD=1`. The `[tool.uv]` table stays
in `pyproject.toml` for anyone who runs uv by hand without `UV_NO_CONFIG`.

To change the lock (a reviewed change, ruling 10.5), run `uv lock` with the same environment, for
example by exporting the variables above. Never edit `uv.lock` by hand.

## Targets

| Target | Runs | Fails when |
| --- | --- | --- |
| `py-tools` | `scripts/py/tools.sh` | a checksum or version does not match the pin |
| `contracts-sync` (`mk/contracts.mk`) | `scripts/contracts/sync.sh` | the clone `../web-block-contracts` (or `CONTRACTS_REPO`) lacks the commit in `contracts.lock`, or `bin/protoc` is not the pinned binary. It copies the files Provisioning reads into `src/contracts/shared/` and writes the generated code to `src/provisioning/infrastructure/grpc/_gen/` |
| `contracts-check` (`mk/contracts.mk`) | `scripts/contracts/sync.sh --check` | either folder differs from what the pinned commit gives. Run it after every change of `contracts.lock` |
| `py-sync` | `tools.sh uv`, `uv lock --check`, `uv sync --locked` | `bin/uv` is not the pinned binary and cannot be fetched, or `uv.lock` does not match `pyproject.toml` |
| `py-fmt-check` | `ruff format --check` | any file would be reformatted |
| `py-lint` | `ruff check` | any finding, banned imports included |
| `py-typecheck` | `mypy` (strict, `src/provisioning` and `tests`) | any error |
| `py-layers` | `lint-imports --no-cache` | any import-linter contract is broken |
| `py-test` | `python -X dev -m pytest -m 'unit or contract' tests/unit tests/contracts` with `PYTHONASYNCIODEBUG=1` | a test fails, a warning is raised (`filterwarnings = ["error"]`), or no test is collected |
| `py-cover` | `python -I scripts/py/cover.py` | see below |
| `py-test-integration`, `py-test-messaging` | pytest `-m integration` / `-m messaging` (Testcontainers) | a test fails or skips; never next to a Kind stack |
| `py-test-jenkins`, `py-test-catalog-live` | pytest `-m jenkins tests/integration/jenkins` / `-m catalog_live tests/integration/catalog` | a test fails or skips; needs `make proto-up` |
| `py-vuln` | `tools.sh osv-scanner`, `python3.14 -I scripts/py/vuln.py` | see below |
| `py-age` | `python3.14 -I scripts/py/age.py` | see below |
| `image-scan` (`mk/image-scan.mk`) | `tools.sh grype`, `python3.14 -I scripts/py/image_scan.py` | see below |

Every gate target except `py-vuln`, `py-age` and `image-scan` depends on `py-sync`, so `make
py-lint` alone also checks the lock. `py-parity` (the Go-to-Python test map) is not defined; it has nothing to compare
against since the
Go reference left this branch.

### py-cover (ruling 10.10, task H1)

1. **Core gates.** `provisioning.core.domain` and `provisioning.core.application` each need 80%
   statement coverage, credited only from their mirror folders `tests/unit/core/domain` and
   `tests/unit/core/application` (one pytest run per package, made by `py-cover` itself).
   - A package with no statements prints `empty (no statements yet)`. That is not a pass.
   - A package with statements and no mirror folder, or a mirror folder without `test_*.py`, fails.
2. **Total gate.** 70% across `provisioning`, combining (`coverage combine`) these suites:

   | Suite | Recorded by | Needs |
   | --- | --- | --- |
   | unit and contract (`tests/unit`, `tests/contracts`) | `py-cover` itself, every run | nothing |
   | `py-test-integration` | `make py-test-integration PY_COVER=1` | Docker (Testcontainers), no Kind stack |
   | `py-test-messaging` | `make py-test-messaging PY_COVER=1` | Docker (Testcontainers), no Kind stack |
   | `proto-test-kind` | `make proto-test-kind PY_COVER=1` | `make proto-up` |
   | `py-test-catalog-live` | `make py-test-catalog-live PY_COVER=1` | `make proto-up` |
   | `py-test-jenkins` | `make py-test-jenkins PY_COVER=1` | `make proto-up` |

   Not included: `tests/e2e` (`proto-e2e`, `proto-demo`). The service runs there as separate
   processes started by `scripts/prototype/run-provisioning.sh`, outside any coverage measurement,
   so the pytest process measures almost nothing of `provisioning`.

   With `PY_COVER=1`, each recording target first runs `cover.py stamp <target>`, which removes the
   target's previous recording and writes `.coverage-data/<target>.stamp.json` (a SHA-256 of every
   `.py` file under `src/provisioning`). Its pytest run then writes `<target>.coverage` and
   `<target>.junit.xml` there (`COVERAGE_FILE` and `PYTEST_ADDOPTS`, set by `py_cover_env` in
   `mk/python.mk`, which `py_prelude` expands, so `proto-test-kind` of `mk/prototype.mk` records
   too). `py-cover` accepts a recording only when all three files exist, the JUnit result shows tests
   that ran with no failure, error or skip, the source digest still matches, and the data names only
   files of this checkout. Otherwise the gate fails and names the command that records the suite
   again; a suite is never skipped. The recordings are checked before the unit suite runs.

   Order on the shared machine (one heavy set at a time):
   ```sh
   make py-test-integration py-test-messaging PY_COVER=1          # no Kind stack up
   make proto-up
   make proto-test-kind py-test-catalog-live py-test-jenkins PY_COVER=1
   make proto-down
   make py-cover                                                   # any time after, same source
   ```

Statement coverage, nothing excluded (`exclude_lines = []`, so no `# pragma: no cover`). Both gates
compare the report's statement count with an inventory of every `.py` file, made with the same
settings file. (Until task H1 the inventory read no settings file, so coverage's built-in exclusions,
`...` stubs and `if TYPE_CHECKING:` blocks, applied to it but not to the report: finding M-1 of
wave 6.)

### py-vuln (OD-57)

`scripts/py/vuln.py`, standard library only, following the discipline of the Go reference's
gate (`scripts/vulndb-fetch.sh`, now only on branch `go-reference`):

1. `tools.sh osv-scanner` verifies `bin/osv-scanner` against its pin.
2. One download per run of `https://osv-vulnerabilities.storage.googleapis.com/PyPI/all.zip` into a
   fresh `bin/.py-vuln.*/`: HTTPS only, bounded, compared with the md5 and the stored length that
   Google Cloud Storage sends (`x-goog-hash`, `x-goog-stored-content-length`). OSV publishes no
   checksum file or signature, so this detects a damaged transfer; authenticity rests on TLS.
3. The archive: every entry a top-level `<ID>.json` holding that advisory with a `modified` date and
   at least one PyPI package; at least 10000 entries; the newest `modified` at most 14 days old and
   not in the future. A stale copy is refused (fails closed).
4. A canary lock pinning `requests 2.19.0` must yield `PYSEC-2018-28` against the same copy.
5. `osv-scanner scan --offline --offline-vulnerabilities -L uv.lock`, with `HOME`, `XDG_*` and
   `OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY` inside the temporary directory (osv-scanner 2.6 reads
   `<dir>/osv-scalibr/PyPI/all.zip`). Every registry package of `uv.lock` must appear as scanned, and
   the copy must be unchanged afterwards.
6. Any finding fails and is listed, unless `scripts/py/vuln-exceptions.toml` names it exactly
   (package, locked version, advisory id) with a reason, a reviewer, an expiry date (valid through
   that day, at most 30 days ahead) and a follow-up. An accepted finding is still printed. An expired
   or too long exception fails the gate; one that matches nothing is reported for removal. Only the
   lead adds one, after the owner signs it (as for `image-scan`).

`PY_VULN_MAX_AGE_DAYS` (default 14) exists only to show the failure path.

### py-age (the 14-day rule)

`scripts/py/age.py` checks that every pin is at least 14 days old:

- each `uv.lock` registry package, dated by the newest of its locked files on PyPI's JSON API
  (`https://pypi.org/pypi/<name>/<version>/json`; every locked SHA-256 must be listed there with the
  upload time the lock recorded);
- each tool pinned for `scripts/py/tools.sh` (`scripts/py/versions.sh`: uv, osv-scanner, goose, protoc,
  grype) and for `scripts/kind-tools.sh` (`deploy/kind/versions.sh`: kind, kubectl, helm, crane, docker-buildx),
  dated by its GitHub release (`https://api.github.com/repos/<repo>/releases/tags/<tag>`;
  unauthenticated, 9 calls per run against a limit of 60 an hour);
- the base image of the service's runtime image (`deploy/image/versions.sh`), dated by the
  publication recorded with its pin (`BASE_IMAGE_PUBLISHED`; decision 0016 section 9 ruling 4); no
  lookup, so the step that moves the pin checks it (`.claude/docs/runbooks/prototype-in-cluster.md`).

Lookups are HTTPS only, 20 seconds each, retried once; a failed lookup fails the gate. A younger pin
passes only with a waiver in `scripts/py/age-waivers.toml` naming it exactly (kind, name, version),
with `reason`, `approved_by` and an unquoted `expires` date. An expired waiver fails; a current
waiver whose pin has aged is printed for removal. The file holds no waiver.

### image-scan (`mk/image-scan.mk`)

`scripts/py/image_scan.py` with Grype (not Trivy: Trivy's release binaries and GitHub actions were
compromised). It builds images, so on the shared machine it runs only in a slot.

1. `tools.sh grype` verifies `bin/grype` against its pin.
2. One database per run into a fresh `bin/.image-scan.*/`: the listing
   `https://grype.anchore.io/databases/v6/latest.json` must be active, schema v6, name a plain
   archive file with a SHA-256 and be built at most 14 days ago; the archive must match the SHA-256;
   `grype db import` loads it, and `grype db status` must report it valid with the listed schema and
   build time. Grype runs with its home inside the temporary directory, no automatic update, and its
   own age limit at 14 days.
3. Each image in `IMAGE_SCAN_TARGETS` (default `sample-site`; `clothing-shop` is the demo's generated
   Next.js shop and needs `registry.npmjs.org`) is built with `docker build`, saved, scanned as
   `docker-archive:` and removed.
   With `bin/docker-buildx` installed (`make kind-tools`), the build runs with the project-local
   `DOCKER_CONFIG` `bin/kind-state/docker-cli` on the daemon of the caller's current Docker context,
   so it uses BuildKit and the sample site's `COPY --chmod` builds (decision 0016 section 9 ruling 5;
   finding F-10 closed). `IMAGE_SCAN_REFS` (`name=reference`, pinned by digest) scans an image already
   built instead, read from its registry, with `IMAGE_SCAN_REGISTRY_CA` as the trusted CA. The
   service image `provisioning` is scanned this way only: the image the cluster run pushed
   (`PROV_IMAGE_ON_HOST` in `bin/kind-state/proto/provisioning/image.env`; `mk/image-scan.mk` shows
   the command).
4. Any High or Critical match fails unless `scripts/py/image-scan-exceptions.toml` names it (image,
   vulnerability, package) with `reason`, `reviewer`, `expires` (an unquoted date at most 30 days
   ahead, decision 0016 section 9 ruling 4) and `follow_up`. An expired exception fails; one that matches nothing is
   printed for
   removal. Only the lead adds exceptions.

`IMAGE_SCAN_MAX_AGE_DAYS` (default 14) exists only to show the failure path.

## Test layout and rules

- `tests/unit/` mirrors the package (`tests/unit/core/domain/...`), `tests/contracts/` holds the
  gate self-tests and contract checks, and `tests/integration/`, `tests/messaging/`, `tests/e2e/` and
  `tests/chart/` hold the suites that need a real dependency. A test anywhere else is refused.
- `tests/conftest.py` applies the marker of each folder (`SUITES`: `unit`, `contract`, `integration`,
  `jenkins`, `kind`, `catalog_live`, `messaging`, `e2e`, `chart`). In every suite but `unit` and `contract` a
  skip or an xfail is reported as a failure, so a missing server fails the run.
- No `__init__.py` under `tests/`; pytest runs with `--import-mode=importlib`. A planted one stops
  the run with a usage error.

## Gate self-tests (`tests/contracts/`)

| Test | Proves |
| --- | --- |
| `test_layers.py` | every import-linter contract is kept on the tree, and planted violations in a temporary copy break the right contract (the Python counterpart of the Go reference's `layers_test.go`) |
| `test_static_bans.py` | ruff flags every banned loader and library under `src/provisioning/`, and only `infrastructure/kubernetes/_client.py` is exempt, from TID251 only |
| `test_skip_is_failure.py` | skips and xfails fail in the dependency suites, stay skips in `unit`, markers follow folders, `__init__.py` is refused |
| `test_import_side_effects.py` | importing every module opens no socket, file or thread (audit hook, PEP 578), and planted side effects are caught |
| `test_wheel_contents.py` | the wheel built by the pinned uv holds `provisioning/py.typed`, every package source, and nothing else |

## Static bans and import contracts

- ruff `banned-api` (TID251): the Kubernetes config loaders of `kubernetes_asyncio`, `kubernetes`
  and `kubernetes.aio` (whole `config` modules and each loader by name), `lightkube`, `requests`,
  `httpx`, `psycopg2`, `jwt.PyJWKClient` and `importlib.import_module`. The only exemption is the
  per-file entry `"src/provisioning/infrastructure/kubernetes/_client.py" = ["TID251"]` in
  `[tool.ruff.lint.per-file-ignores]`. A per-file ignore cannot name single banned entries, so inside
  that file every banned API is allowed: its review must check that it imports only the Kubernetes client.
- The import-linter contracts are listed in `pyproject.toml` (`make py-layers`). The former contract
  `hosts-not-wired` is removed: the hosts now wire the stores and loops it kept out.

## The Go reference

- Until the Go removal (decision `0014` section 18), Go and Python shared this branch: the Makefile
  ended with `include mk/python.mk`, its `tools` target installed goose through
  `scripts/py/tools.sh goose`, and the Go and Python tests shared `tests/contracts`,
  `tests/integration` and `tests/messaging`, each runner collecting only its own files.
- The Go code, its Make targets (`tools`, `fmt-check`, `build`, `vet`, `lint`, `test`, `cover`,
  `cover-all`, `vuln`, `generate`, `xr-validate`, `function-*`, `kind-up` and the rest of the Phase 4
  Kind stack), its tool pins (golangci-lint, govulncheck, sqlc, the crossplane CLI) and its tests are
  kept on branch `go-reference` and the tags `go-reference-phase-4`, `g4-acceptance-wip` and
  `f2-guarded-delete-wip`, never merged back.
- The goose binary at `bin/goose` is still the checksum-pinned release binary from
  `scripts/py/tools.sh`; the Makefile's `migrate-up` and `migrate-status`, `py-test-integration` and
  the prototype stack use it.
