---
paths:
  - "README.md"
  - "AGENTS.md"
  - ".claude/**/*.md"
---

# Documentation

**`README.md` is the contract other teams read.** Section map:

| Section | Covers |
|---|---|
| 1 | Architecture, LangGraph and security |
| 2 | `.env` |
| 3 | `/internal/v1` |
| 4 | RabbitMQ events |
| 5 | Outbound HTTP |
| 6 | Public `/v1`, and the generated site's database (the shared `catalog` schema, and its differences from the design) |
| 7 | Codegen modes |
| 8 | Quick start |
| 9 | Product portal and single sign-on (Keycloak): how it works, setup (including the catalog DBA steps, 9.7 C), `site_env` contract, troubleshooting |
| 10 | Platform integration: `PUBLISH_TARGET=project`, Provisioning's runtime contract for generated sites, what is not aligned yet |

- Document behaviour that exists in the code. Read the code first, and quote real field names, status codes and examples. Describe planned work as planned.
- Endpoint and event examples must match the Pydantic models: snake_case for events (the platform envelope), camelCase inside the `{success, data, meta}` envelope for the `/internal/v1` endpoints Project reads, snake_case for the other REST bodies.
- Mermaid diagrams in section 1 must keep the real node names (`prepare`, `ai_code_update`, `build`, `compiler_repair`, `github_push`, `deploy`, `handle_failure`).
- **Mermaid syntax that GitHub can render:**
  - Never use reserved words (`graph`, `end`, `subgraph`, `flowchart`, `style`, `class`, `click`) as node ids or `classDef` names. `classDef graph` broke the section 1 diagram.
  - Write line breaks in labels as `<br/>`, not `\n`.
  - Quote any edge label that contains `<`, `>`, `/`, `{`, `(` or `:` (`-->|"text"|`).
  - Parse every edited block with mermaid (for example `npx -p @mermaid-js/mermaid-cli mmdc -i file.mmd`) before you call it done.

**`.claude/` docs (rules, skills, agents, `CLAUDE.md`) are instructions for Claude.**
- Each fact lives in one place. Area detail goes in a path-scoped rule, and `.claude/CLAUDE.md` keeps only what every session needs.
- Write rules as imperatives with the reason, and point at a real file/function rather than pasting code that will rot.
- When code changes a convention, update the rule or skill in the same change. A stale rule is worse than none.
