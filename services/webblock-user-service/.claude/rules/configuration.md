---
paths:
  - "**/*.yml"
  - "**/*.yaml"
  - "config-repo/**"
---

# Configuration

- YAML only. Never write `key=value` (properties syntax) in a `.yml` file; the
  `check-yaml` hook rejects it.
- Profiles: default = local dev (Eureka on). `application-k8s.yml` = Kubernetes
  (Eureka off, simple discovery instances → Service DNS). See `microservice-boundaries.md`.
- Spring Boot 4.1.1 + Spring Cloud **2025.1.3** in every Java service. Same plugin and BOM
  versions everywhere; never put versions on Spring Boot / Spring Cloud artifacts.
- `config-repo/` holds `<app-name>-<profile>.yml` placeholders. There is no config-server yet;
  don't add `spring.config.import: configserver:` until one exists.
- **No secrets in any committed file.** Use `${ENV_VAR}` or `${ENV_VAR:dev-default}`;
  document variables in `.env.example`.
- Ports follow the table in `CLAUDE.md`; don't reassign an existing port.
- Custom properties use the `webblock.` prefix, bound with `@ConfigurationProperties`.
  Values the spec says "can change" (retry limit 2, trial 14 days, 30 products) live here or in the DB.
