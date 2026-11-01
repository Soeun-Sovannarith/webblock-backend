---
name: add-setting
description: Add a configuration setting / environment variable to the service (Settings field, .env.example, README section 2, tests). Use when code needs a new knob, URL, timeout, feature flag or secret.
argument-hint: "<SETTING_NAME and purpose>"
---

# Add a setting

Rules: `.claude/rules/config-env.md`.

1. **`app/core/config.py`**: add a typed field to `Settings` next to related ones. Give it a safe local default, and use `Literal[...]` for a fixed set of values. A secret defaults to `""`.
   ```python
   build_network_install: Literal["bridge", "none"] = "bridge"
   ```
2. **`.env.example`**: add `UPPER_SNAKE=<default>` in the matching group, with a one-line `#` comment above it saying what it does and the allowed values. Leave secret values empty. Don't touch `.env`: it is the user's and is denied. Tell the user to add the variable there if they need a non-default value.
3. **Use it** through `from app.core.config import settings`. Never use `os.environ`.
4. **README section 2**: add it to the matching block of the `.env` listing.
5. **Test** with `monkeypatch.setattr(settings, "field", value)` for each behaviour it switches.
6. If it changes runtime behaviour that the session-context hook or status line reports (`BUILD_RUNNER`, `CODEGEN_MODE`, …), consider adding it there too (`.claude/hooks/session-context.sh`). Only non-secret values.
