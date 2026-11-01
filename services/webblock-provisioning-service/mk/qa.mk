# The code quality sweep (decision 0026 Q5), included by the Makefile with one line. It is not a gate:
# it prints every finding for the qa-reviewer agent to triage (.claude/skills/qa-sweep/SKILL.md) and
# fails only when a tool itself could not run. Nothing here reads .env or touches a cluster.

.PHONY: qa-sweep

# Generated protobuf code (decision 0022 E3) is not handwritten, so the sweep skips it.
QA_GEN_DIR := src/provisioning/infrastructure/grpc/_gen
QA_RUFF_RULES := ERA,PIE,RET,FAST,DTZ,C90

# Each step runs even when an earlier one found something. Exit status of each tool: pylint is a bit
# mask where 1 is fatal and 32 a usage error, the rest are message classes; vulture 3 means dead code
# was found; ruff and mypy 1 mean findings. Any other status is a broken run and fails the target.
qa-sweep: py-sync
	@$(py_prelude) broken=0; \
	echo '== 1/4 duplicate code: pylint R0801, at least 8 similar lines, src/'; \
	'$(PY_BIN)/pylint' --disable=all --enable=duplicate-code --min-similarity-lines=8 \
	  --ignore-paths='^$(QA_GEN_DIR)/.*' --score=n src/provisioning; rc=$$?; \
	if [ $$((rc & 33)) -ne 0 ]; then echo "qa-sweep: pylint did not run (exit $$rc)" >&2; broken=1; fi; \
	echo '== 2/4 dead code: vulture, src/ and scripts/py/vulture_whitelist.py'; \
	'$(PY_BIN)/vulture' src/provisioning scripts/py/vulture_whitelist.py --exclude '$(QA_GEN_DIR)'; rc=$$?; \
	if [ $$rc -ne 0 ] && [ $$rc -ne 3 ]; then echo "qa-sweep: vulture did not run (exit $$rc)" >&2; broken=1; fi; \
	echo '== 3/4 ruff: $(QA_RUFF_RULES)'; \
	'$(PY_BIN)/ruff' check --select '$(QA_RUFF_RULES)' --output-format concise; rc=$$?; \
	if [ $$rc -gt 1 ]; then echo "qa-sweep: ruff did not run (exit $$rc)" >&2; broken=1; fi; \
	echo '== 4/4 mypy: deprecated APIs (enable_error_code in pyproject.toml)'; \
	'$(PY_BIN)/mypy'; rc=$$?; \
	if [ $$rc -gt 1 ]; then echo "qa-sweep: mypy did not run (exit $$rc)" >&2; broken=1; fi; \
	echo '== qa-sweep done: triage the findings above with the qa-sweep skill'; \
	exit $$broken
