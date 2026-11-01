---
paths:
  - "app/core/config.py"
  - ".env.example"
  - "docker-compose.yml"
---

# Settings and environment

- **One place per setting.** A field on `Settings` (`app/core/config.py`, pydantic-settings, read from `.env`) plus a line in `.env.example` with a one-line `#` comment above it, plus the matching block in `README.md` section 2. Use the add-setting skill.
- **Safe defaults.** The default must work for a local run without external services: `BUILD_RUNNER=docker`, `DEPLOYMENT_PROVIDER=none`, `RUN_CONSUMER` off, empty secrets. Production-only strictness (`STRICT_PROMOTION=true`) is opt-in.
- **Constrained choices use `Literal[...]`** (see `codegen_mode`, `build_runner`), so a typo fails at startup, not mid-run.
- **Secrets** (`GLM_API_KEY`, `GITHUB_TOKEN`, `DEPLOYMENT_WEBHOOK_SECRET`, `GLM_CF_ACCESS_*`, `KEYCLOAK_ADMIN_CLIENT_SECRET`, `KEYCLOAK_CF_ACCESS_*`, `SITE_DB_SECRET`, `SITE_STORAGE_SECRET_ACCESS_KEY`) default to `""` and appear in `.env.example` with no value. Never log them, never put them in run logs or check reports, and never read `.env` (it is denied).
- **Ports must agree.** Compose maps Postgres to `localhost:5433` (`webblock`/`webblock`). `config.py`'s built-in `DATABASE_URL` (5434, `aicode_db`) deliberately does not match, so `.env` is required. Don't "fix" one side without the other.
- **Tests change settings with `monkeypatch.setattr(settings, "<field>", value)`.** Never with environment variables.
