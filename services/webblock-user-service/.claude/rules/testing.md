---
paths:
  - "**/src/test/**"
  - "**/*.test.ts"
  - "**/*.test.tsx"
  - "**/*.spec.ts"
  - "apps/web/e2e/**"
---
# Tests

- A bug fix starts with a failing test that reproduces it.
- Test names say the rule: `rejectsSecondFreeTrialForSameClient`, `skipsCommandWithOldCommandNumber`.
- Cover the business rules in `docs/specs/business-rules.md` that the code touches, incl. the "No" branch of each workflow decision ("Is OTP valid?", "Is subscription active?", "Is max retry exceeded?").
- Backend: unit tests for domain rules, `@WebMvcTest` for controllers (incl. 401/404 for other owners), Testcontainers for repositories and migrations.
- Web: Vitest + Testing Library for components; Playwright for main flows (register → create website → deploy).
- No real WeBill, Keycloak, or LLM calls in tests — use WireMock / fakes.
