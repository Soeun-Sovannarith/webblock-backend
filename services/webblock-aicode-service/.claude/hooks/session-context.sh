#!/usr/bin/env bash
# SessionStart: tell Claude what state the local environment is in, so it doesn't guess (or run things that can't work).
# Prints only non-secret settings from .env; stdout becomes session context.
cd "$CLAUDE_PROJECT_DIR" || exit 0
port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
env_value() { [ -f .env ] && grep -E "^$1=" .env | tail -n1 | cut -d= -f2-; }

echo "## Local environment (session start)"
echo "- Branch: $(git branch --show-current 2>/dev/null || echo '?'); uncommitted files: $(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
[ -x .venv/bin/python ] && echo "- venv: .venv present" || echo "- venv: MISSING (python -m venv .venv && .venv/bin/pip install -e \".[dev]\")"
if [ -f .env ]; then
  echo "- .env: present. BUILD_RUNNER=$(env_value BUILD_RUNNER || true) CODEGEN_MODE=$(env_value CODEGEN_MODE || true) RUN_WORKER=$(env_value RUN_WORKER || true) STRICT_PROMOTION=$(env_value STRICT_PROMOTION || true)"
  [ -n "$(env_value GLM_API_KEY)" ] && echo "- GLM: API key set" || echo "- GLM: no API key, so GLM calls and compiler_repair are unavailable"
else
  echo "- .env: MISSING (cp .env.example .env; config.py's built-in DATABASE_URL does not match compose)"
fi
port_open 5433 && echo "- Postgres :5433: up" || echo "- Postgres :5433: down (docker compose up -d). The *_not_found tests in test_internal_routes.py will fail."
port_open 8000 && echo "- API :8000: running" || echo "- API :8000: not running"
port_open 5672 && echo "- RabbitMQ :5672: up" || echo "- RabbitMQ :5672: down (only needed with RUN_CONSUMER=true)"
exit 0
