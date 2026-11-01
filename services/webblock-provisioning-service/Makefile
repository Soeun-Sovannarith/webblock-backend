# Targets of the Provisioning service. Target names are a contract used by CI and the independent
# validator. The Python gates are in mk/python.mk, the prototype stack in mk/prototype.mk and the
# runtime image scan in mk/image-scan.mk, the Helm chart in mk/chart.mk and the pinned contracts in
# mk/contracts.mk, all included at the end. The Go reference and its targets
# (fmt-check, build, vet, lint, test, cover, vuln, generate, xr-validate, function-*, kind-up and the
# rest of the Phase 4 Kind stack) were removed from this branch; they live on branch go-reference and
# the tags go-reference-phase-4, g4-acceptance-wip and f2-guarded-delete-wip (decision 0014 section 18).
#
# A bare make runs help, which only prints where the targets are; it builds and installs nothing.

# Database credentials are never read by make. Only migrate-up and migrate-status load .env, inside
# their recipes (see load_env), so every other target runs without PG_USER or PG_PASS in its
# environment.

BIN := $(CURDIR)/bin

# Migrations run with current role provisioning_migrator (a login role that is a member of it, or a
# superuser, may SET ROLE to it). Roles are created once by scripts/db/bootstrap-roles.sql. bin/goose
# is the pinned, checksum-verified goose installed by make py-tools (scripts/py/tools.sh).
MIGRATOR_ROLE := provisioning_migrator

.DEFAULT_GOAL := help

.PHONY: help kind-tools migrate-up migrate-status

help:
	@echo 'Python gates: mk/python.mk (.claude/docs/runbooks/python-p0.md). Prototype stack and live suites:'
	@echo 'mk/prototype.mk (.claude/docs/runbooks/prototype-stack.md). Image scan: mk/image-scan.mk.'
	@echo 'Helm chart: mk/chart.mk (make chart-check). Shared contracts: mk/contracts.mk'
	@echo '(make contracts-sync contracts-check). Code quality sweep, not a gate: mk/qa.mk (make qa-sweep).'
	@echo 'Also: kind-tools, migrate-up and migrate-status PG_DATABASE=<name>.'

# The pinned, checksum-verified kind, k3d, kubectl, helm, crane and docker-buildx in ./bin (scripts/kind-tools.sh,
# pins in deploy/kind/versions.sh). The prototype stack (mk/prototype.mk) and the live Kind suites use them.
kind-tools:
	bash scripts/kind-tools.sh

# Shell snippet for the two migration targets. It parses .env as data and never executes or expands
# anything in it: each line is blank, a # comment, or NAME=value (an "export " prefix is allowed). A
# value is a run of letters, digits and _.,:/@%+=- and/or quoted parts: '...' is taken literally, \'
# is one single quote, and "..." is literal but may not hold $, ` or \. Every accepted line means the
# same to a POSIX shell, so quoting follows the shell rules in .claude/docs/runbooks/phase-1-database.md.
# Any other line stops the recipe with the file name and line number; no part of the line is printed.
# Values in .env replace variables already in the environment. A missing .env is not an error.
# Nothing is echoed. Used inside a recipe, never as make syntax.
define load_env
_env_bad() { echo ".env: line $$1: $$2; nothing was run (see .claude/docs/runbooks/phase-1-database.md, How .env is read)" >&2; exit 1; }; \
if [ -e .env ]; then \
  if [ ! -f .env ] || [ ! -r .env ]; then echo ".env: not a readable regular file" >&2; exit 1; fi; \
  _env_tab="$$(printf '\t')"; _env_n=0; \
  while IFS= read -r _env_l || [ -n "$$_env_l" ]; do \
    _env_n=$$((_env_n + 1)); \
    _env_l="$${_env_l#"$${_env_l%%[! $$_env_tab]*}"}"; \
    _env_l="$${_env_l%"$${_env_l##*[! $$_env_tab]}"}"; \
    case "$$_env_l" in ''|'#'*) continue;; *=*) ;; *) _env_bad "$$_env_n" "expected NAME=value";; esac; \
    _env_k="$${_env_l%%=*}"; _env_k="$${_env_k#export }"; _env_r="$${_env_l#*=}"; _env_v=; \
    case "$$_env_k" in ''|[!A-Za-z_]*|*[!A-Za-z0-9_]*) _env_bad "$$_env_n" "expected NAME=value";; esac; \
    while [ -n "$$_env_r" ]; do \
      case "$$_env_r" in \
        "'"*"'"*) _env_r="$${_env_r#?}"; _env_v="$$_env_v$${_env_r%%"'"*}"; _env_r="$${_env_r#*"'"}";; \
        '"'*'"'*) _env_r="$${_env_r#?}"; _env_s="$${_env_r%%'"'*}"; _env_r="$${_env_r#*'"'}"; \
          case "$$_env_s" in *[\$$\`\\]*) _env_bad "$$_env_n" "a double-quoted value holds a character that needs single quotes";; esac; \
          _env_v="$$_env_v$$_env_s";; \
        "\\'"*) _env_v="$$_env_v'"; _env_r="$${_env_r#??}";; \
        *) _env_s="$${_env_r%%[!A-Za-z0-9_.,:/@%+=-]*}"; \
          if [ -z "$$_env_s" ]; then _env_bad "$$_env_n" "the value needs single quotes or has an unclosed quote"; fi; \
          _env_v="$$_env_v$$_env_s"; _env_r="$${_env_r#"$$_env_s"}";; \
      esac; \
    done; \
    export "$$_env_k=$$_env_v" || _env_bad "$$_env_n" "this variable cannot be set"; \
  done < .env; \
fi;
endef

# The DSN carries no credentials: user and password reach goose through PGUSER and PGPASSWORD, so
# special characters need no URL escaping. PG_DATABASE, PG_HOST and PG_PORT are pasted into the DSN
# and are therefore checked against conservative patterns first. Recipe lines use @ so nothing is echoed.
define goose_env
$(load_env) \
host="$${PG_HOST:-localhost}"; port="$${PG_PORT:-5432}"; \
if [ -z "$${PG_DATABASE:-}" ]; then echo "PG_DATABASE is not set; refusing to migrate an unspecified database"; exit 1; fi; \
if [ -z "$${PG_USER:-}" ]; then echo "PG_USER is not set"; exit 1; fi; \
if ! printf '%s' "$$PG_DATABASE" | grep -Eq '^[a-z_][a-z0-9_]{0,62}$$'; then echo "PG_DATABASE must match ^[a-z_][a-z0-9_]{0,62}$$"; exit 1; fi; \
if ! printf '%s' "$$host" | grep -Eq '^[A-Za-z0-9._-]{1,253}$$'; then echo "PG_HOST must match ^[A-Za-z0-9._-]{1,253}$$"; exit 1; fi; \
if ! printf '%s' "$$port" | grep -Eq '^[0-9]{1,5}$$'; then echo "PG_PORT must be 1 to 5 digits"; exit 1; fi; \
export PGUSER="$$PG_USER" PGPASSWORD="$${PG_PASS:-}" GOOSE_DRIVER=postgres GOOSE_MIGRATION_DIR=src/migrations \
  GOOSE_DBSTRING="postgres://$$host:$$port/$$PG_DATABASE?options=-c%20role%3D$(MIGRATOR_ROLE)";
endef

migrate-up:
	@$(goose_env) exec $(BIN)/goose up

migrate-status:
	@$(goose_env) exec $(BIN)/goose status

# Python targets (decision 0013 section 10.2) and the prototype stack (decision 0014).
include mk/python.mk
include mk/prototype.mk
include mk/image-scan.mk
include mk/chart.mk
include mk/contracts.mk
include mk/qa.mk
