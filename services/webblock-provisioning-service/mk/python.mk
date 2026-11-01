# Python port targets (decision 0013 section 10.2), included by the Makefile with one line.
# .claude/docs/runbooks/python-p0.md explains each target. None of them reads .env.
#
# py-parity (the Go-to-Python test parity map) is dropped with the Go reference, which lives on branch
# go-reference (decision 0014 section 18). The runtime image scan (image-scan) is in mk/image-scan.mk.

.PHONY: py-tools py-sync py-fmt-check py-lint py-typecheck py-layers py-test py-cover py-test-integration \
  py-test-messaging py-test-jenkins py-test-catalog-live py-vuln py-age spec-check

PY_VENV := $(CURDIR)/.venv
PY_BIN  := $(PY_VENV)/bin
UV      := $(BIN)/uv

# Environment of every uv call (ruling 10.4): the venv at the module root, the uv cache in the shared
# bin/, the system Python 3.14 only, no uv.toml or [tool.uv] discovery. Because UV_NO_CONFIG=1 also
# makes uv ignore the [tool.uv] table of pyproject.toml, its two settings are passed explicitly:
# UV_NO_BUILD=1, and UV_EXCLUDE_NEWER read from pyproject.toml by py_prelude (one source of truth).
PY_UV_ENV := UV_PROJECT_ENVIRONMENT='$(PY_VENV)' UV_CACHE_DIR='$(CURDIR)/bin/uv-cache' \
  UV_PYTHON_DOWNLOADS=never UV_PYTHON_PREFERENCE=only-system UV_PYTHON=python3.14 \
  UV_NO_CONFIG=1 UV_NO_BUILD=1

# Shell snippet run first by every Python target: refuse anything but the system Python 3.14,
# drop interpreter variables from the caller's shell, read exclude-newer from pyproject.toml, and
# export the uv environment. Used inside a recipe, never as make syntax.
define py_prelude
command -v python3.14 >/dev/null 2>&1 || { echo "python3.14 is not on PATH; the Python targets use the system Python 3.14 (decision 0013 section 10.4)" >&2; exit 2; }; \
python3.14 -I -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 14))' || { echo "python3.14 does not report version 3.14 (decision 0013 section 10.4)" >&2; exit 2; }; \
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONUSERBASE VIRTUAL_ENV; export PYTHONNOUSERSITE=1; \
_xn="$$(python3.14 -I -c 'import tomllib; f = open("pyproject.toml", "rb"); print(tomllib.load(f)["tool"]["uv"]["exclude-newer"]); f.close()')" || { echo "cannot read [tool.uv] exclude-newer from pyproject.toml" >&2; exit 2; }; \
export UV_EXCLUDE_NEWER="$$_xn" $(PY_UV_ENV); \
$(py_cover_env)
endef

# Coverage recording for the total gate of py-cover (task H1; scripts/py/cover.py RECORDED_SUITES).
# Run one of the targets in PY_COVER_SUITES with PY_COVER=1 and it records into .coverage-data/:
# cover.py stamp removes the target's previous recording and writes a digest of the source, then
# PYTEST_ADDOPTS makes its pytest run write <target>.coverage and <target>.junit.xml. py-cover
# accepts a recording only when all three files exist, the run passed with no skip, and the source
# is unchanged. Expanded inside py_prelude, so it applies to the targets of mk/prototype.mk as well
# (proto-test-kind); for any other target, or without PY_COVER=1, it expands to nothing.
PY_COVER_SUITES := py-test-integration py-test-messaging py-test-jenkins py-test-catalog-live \
  proto-test-kind
define py_cover_env
$(if $(and $(filter 1,$(PY_COVER)),$(filter $@,$(PY_COVER_SUITES))),\
'$(PY_BIN)/python' -I scripts/py/cover.py stamp '$@' || exit 2; \
export COVERAGE_FILE='$(CURDIR)/.coverage-data/$@.coverage' \
  PYTEST_ADDOPTS="$${PYTEST_ADDOPTS:+$$PYTEST_ADDOPTS }--cov --cov-report= --junitxml=$(CURDIR)/.coverage-data/$@.junit.xml";)
endef

# The pinned, checksum-verified uv, osv-scanner, goose, grype and protoc (scripts/py/tools.sh,
# ruling 10.3; protoc for make contracts-sync, decision 0022 E3).
py-tools:
	bash scripts/py/tools.sh

# Checks that bin/uv is the pinned binary (downloading it only when it is missing or wrong), that
# uv.lock matches pyproject.toml, and installs exactly the locked set into .venv.
py-sync:
	@$(py_prelude) \
	bash scripts/py/tools.sh uv && \
	'$(UV)' lock --check && \
	'$(UV)' sync --locked

py-fmt-check: py-sync
	@$(py_prelude) exec '$(PY_BIN)/ruff' format --check

py-lint: py-sync
	@$(py_prelude) exec '$(PY_BIN)/ruff' check

py-typecheck: py-sync
	@$(py_prelude) exec '$(PY_BIN)/mypy'

py-layers: py-sync
	@$(py_prelude) exec '$(PY_BIN)/lint-imports' --no-cache

# The spec register .claude/docs/requirements/spec.yaml against BRD, PRD, FRD and TRD, the test IDs in
# tests/, the commit messages of main..HEAD, the designs and the plans (scripts/py/spec_check.py,
# standard library only, no venv). The checker's own cases run first and fail the target when broken.
# A report, not a gate yet: it prints the counts and every problem and exits 0;
# SPEC_CHECK_FLAGS=--strict makes a problem fail.
spec-check:
	@$(py_prelude) python3.14 -I scripts/py/spec_check.py --self-test >/dev/null \
	  || { echo "spec-check: the checker's own cases fail (python3.14 scripts/py/spec_check.py --self-test)" >&2; exit 2; }; \
	exec python3.14 -I scripts/py/spec_check.py $(SPEC_CHECK_FLAGS)

# Unit and contract tests, with asyncio debug mode and the development mode of the interpreter;
# pytest's filterwarnings = ["error"] turns every warning into a failure. pytest exits non-zero when
# it collects no test.
PY_TEST_DIRS = $(wildcard tests/unit tests/contracts)

py-test: py-sync
	@$(py_prelude) \
	if [ -z '$(PY_TEST_DIRS)' ]; then echo "py-test: neither tests/unit nor tests/contracts exists" >&2; exit 1; fi; \
	PYTHONASYNCIODEBUG=1 exec '$(PY_BIN)/python' -X dev -m pytest -m 'unit or contract' $(PY_TEST_DIRS)

# Ruling 10.10 and task H1: each core package at 80%, credited only from its mirror folder under
# tests/unit; then 70% across the package from the unit suite (run here) combined with the recordings
# of py-test-integration, py-test-messaging, proto-test-kind, py-test-catalog-live and
# py-test-jenkins, each made earlier with PY_COVER=1 in its own machine slot (scripts/py/cover.py).
# A missing, failed, skipped or stale recording fails the gate.
py-cover: py-sync
	@$(py_prelude) PYTHONASYNCIODEBUG=1 exec '$(PY_BIN)/python' -I scripts/py/cover.py

# Real PostgreSQL 18 through Testcontainers (task B2, decision 0014): the migrations, their guards and
# grants. A missing Docker or server fails the run. Needs bin/goose (make py-tools) and Docker; it
# never connects to a server it did not start, and reads no .env. One server at a time: do not run it
# next to a Kind cluster or another Testcontainers suite (decision 0009 section 1).
py-test-integration: py-sync
	@$(py_prelude) exec '$(PY_BIN)/python' -X dev -m pytest -m integration tests/integration

# Real RabbitMQ through Testcontainers (task E2, decision 0014): the adapter, the worker loops over a
# broker and the event simulator. It starts its own rabbitmq:4.3.6-management-alpine, never connects
# to a broker it did not start (the owner's rabbitmq container included) and reads no .env. A
# missing Docker or broker fails the run. Prototype rule (0014): it may run next to one Kind cluster
# while free -m shows at least 2000 MB available.
py-test-messaging: py-sync
	@$(py_prelude) exec '$(PY_BIN)/python' -X dev -m pytest -m messaging tests/messaging

# The prototype stack's Jenkins (decision 0014): tests/integration/jenkins. Needs make proto-up; fails,
# never skips, when the stack is down.
py-test-jenkins: py-sync
	@$(py_prelude) exec '$(PY_BIN)/python' -X dev -m pytest -m jenkins tests/integration/jenkins

# The prototype stack's catalog database and Kind (decision 0014 section 14): tests/integration/catalog.
# Needs make proto-up; fails, never skips, when the stack is down.
py-test-catalog-live: py-sync
	@$(py_prelude) exec '$(PY_BIN)/python' -X dev -m pytest -m catalog_live tests/integration/catalog

# OD-57 (task H1): osv-scanner, verified against its pin, offline on uv.lock against one verified
# download of the OSV PyPI advisories per run (md5 and length as sent, layout, at least 10000
# advisories, newest at most 14 days old, a canary finding); any finding fails (scripts/py/vuln.py).
py-vuln:
	@$(py_prelude) bash scripts/py/tools.sh osv-scanner && exec python3.14 -I scripts/py/vuln.py

# The 14-day version-age rule (task H1): every pin of uv.lock (dated by PyPI's JSON API) and every
# tool pinned for scripts/py/tools.sh and scripts/kind-tools.sh (dated by its GitHub release) is at
# least 14 days old, unless scripts/py/age-waivers.toml waives it with a reason, an approver and an
# expiry. A failed lookup fails the gate (scripts/py/age.py).
py-age:
	@$(py_prelude) exec python3.14 -I scripts/py/age.py
