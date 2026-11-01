# Security review: prototype-only OpenAPI description and Swagger UI (springdoc)

Stage 5 (Verify). Branch `swagger-prototype`, range `main...HEAD` (b7936e0 spec, 5520aa1 implementation, 29a9efa
Jackson 2 override). Reviewer model: Claude Opus 5.5 (`claude-opus-5-5`). In scope: TR-40 (parents FR-1 to FR-5,
FR-8 to FR-25; the commits cite FR-8) and TR-7 (the Jackson 2 override). Articles checked: Art. II, III, VI.

## Method

Skills: `differential-review` (diff, blast radius, untested changes), `sharp-edges` (configuration cliffs, auth
patterns, Java). The only review of this change (owner decision), so the framework behaviour behind each claim was
checked in the resolved jars (`javap` on the Gradle cache copies), not assumed.

Commands and results:

| Command | Result | Notes |
| --- | --- | --- |
| `git diff main...HEAD` (20 files) | read in full | code, config, lockfile, verification metadata, docs |
| `javap` on springdoc 3.1.1 `SpringDocConfiguration`, `SpringDocWebMvcConfiguration`, `SwaggerConfig` | read | all gated by `@ConditionalOnProperty(name=..., matchIfMissing=true)`, no `havingValue` |
| `javap` on Boot 4.1.1 `OnPropertyCondition$Spec.isMatch` | read | no `havingValue`: matches unless the value equals `false` (ignoring case) |
| `javap` on Spring Web 7.0.9 `DefaultHttpMessageConverters$DefaultBuilder` | read | Jackson 3 JSON and YAML win over Jackson 2 (else-if chains) |
| `javap` on springdoc `AbstractOpenApiResource.getOpenApi(String, Locale)`, `OpenAPIService.updateServers` | read | cached OpenAPI mutated per request, returned after unlock |
| `javap` on springdoc `SwaggerUiConfigParameters`, `AbstractSwaggerUiConfigProperties`; swagger-ui 5.32.14 bundle | read | `queryConfigEnabled` unset (UI default `false`), `validatorUrl` sent as `""`, `persistAuthorization` false |
| `curl -I` Maven Central for 7 new or newly runtime artifacts | Last-Modified read | all at least 14 days before the commits (2026-10-30) |
| `scripts/osv-scan.sh` (LF copy of the script and of `HEAD:gradle.lockfile`, pinned osv-scanner v2.6.0 checksum-verified, run in `postgres:18` with Git's CA bundle) | passed | 316 packages, "No issues found", exit 0 |
| OSV API query for npm `swagger-ui` and `swagger-ui-dist` 5.32.14 | passed | no advisories |
| `./gradlew test --tests '*ProfileGuardTest' --offline` (worktree) | passed 33 | 0 failures, 0 skipped |
| `python3 -I scripts/spec-check.py` | 0 errors | report only; Must FRs with no test: 28 |

Not run, and why: `./gradlew check` and `integrationTest` (`OpenApiPrototypeTest`, `ProductionStartupTest`) because a
Testcontainers suite was already running on the shared machine (one suite at a time); the implementer owns the gate
results. No live request was sent: path traversal, encoded slashes and matrix parameters were checked against the
framework defaults (default `StrictHttpFirewall`, no `HttpFirewall` or `WebSecurityCustomizer` bean, no servlet path)
rather than by a request, since a reviewer may not add a test.

## Findings

### L1. low: ProfileGuard and springdoc read "off" differently; `off`, `no`, `0` turn springdoc on in production

- Where: `src/main/java/org/kshrd/webblock/project/common/infrastructure/config/ProfileGuard.java:140-146`.
- Breaks: TR-40 ("ProfileGuard refuses to start production with either on"); Art. VI (production holds no prototype
  aid). Defence in depth only, see impact.
- Why: the guard binds the settings as `Boolean`. Spring's converter reads `false`, `off`, `no`, `0` (trimmed) as
  false, so the guard accepts them. springdoc's auto-configurations use `@ConditionalOnProperty` without
  `havingValue`, and Boot 4.1.1 `OnPropertyCondition` matches every value except the string `false`. So
  `SPRINGDOC_SWAGGERUI_ENABLED=off`, `springdoc.api-docs.enabled=0` or `"false "` passes the guard and creates
  `SpringDocConfiguration`, `SpringDocWebMvcConfiguration` (`OpenApiWebMvcResource`) and `SwaggerConfig` in production.
  The comment at line 137 ("bound as springdoc reads them") is therefore not true for these values.
- Abuse: an operator who writes `off` in the production Deployment, believing it disables the docs, gets the springdoc
  handlers mapped in production. Today they still answer 401 or 403, because production has no `apiDocsSecurity` chain
  and `apiSecurity` ends in `anyRequest().denyAll()` (`SecurityConfig.java:100-101`); the exposure needs a second
  mistake (a later permit on these paths, or springdoc moved to the management port).
- Fix: in `refuseApiDocs`, read the raw values with `environment.getProperty(name)` and refuse unless each is present
  and, after `strip()`, equals `false` ignoring case (that is exactly springdoc's off). Add `off`, `no`, `0` and
  `" false"` rows to `tr40_productionRefusesTheOpenApiDescriptionAndSwaggerUi`.

### L2. low: the description's server URL is taken from request Host and can leak a pasted token to another host

- Where: `src/main/java/org/kshrd/webblock/project/common/infrastructure/config/OpenApiConfig.java:29-40` (no
  `servers`), with springdoc 3.1.1 `AbstractOpenApiResource.getOpenApi(String, Locale)`.
- Breaks: Art. III ("Tokens are never forwarded"); TR-40 (the Bearer scheme is for the Project API only).
- Why: with no server in the `OpenAPI` bean, springdoc writes `servers[0].url` from each request (Host header, or
  `X-Forwarded-Host` when Tomcat's remote IP valve trusts the hop). On a cache hit it calls `updateServers` on the one
  shared cached `OpenAPI`, unlocks, and returns that same object for serialisation outside the lock, so a concurrent
  request can change the URL another caller receives.
- Abuse: on a prototype instance others can reach (a shared Kind or demo slot), an attacker floods `/v3/api-docs` with
  `Host: collect.attacker.example`. A developer who opens Swagger UI in that window gets the attacker's server. After
  Authorize (a dev token, or a real Keycloak token in jwt mode) and Execute, the browser sends a CORS preflight, which
  the attacker's host approves, then `Authorization: Bearer <token>` to that host. It needs reachability, a lost race
  and an unnoticed server line, so it is low.
- Fix: declare a relative server in `OpenApiConfig`, for example `.servers(List.of(new Server().url("/")))`. springdoc
  then sets `isServersPresent` and never rewrites it, and Swagger UI calls the origin it was loaded from. A test can
  assert `$.servers[0].url == "/"` with a foreign Host header.

### L3. low: commons-lang3 3.20.0 now reaches the production runtime but is not recorded

- Where: `gradle.lockfile:130` (`productionRuntimeClasspath,runtimeClasspath` added);
  `.claude/docs/dependency-versions.md`
  has no row for it.
- Breaks: Art. II (every dependency is recorded in `.claude/docs/dependency-versions.md`).
- Why: swagger-core 2.2.55 pulls it in. Before this branch it was only on test and PMD paths. The branch records the
  other artifacts that come through springdoc (swagger-core, swagger-ui, webjars-locator-lite) but not this one.
  It is Apache-2.0, published 2025-11-12, clear of CVE-2025-48924 (fixed in 3.18.0), and the OSV scan is clean, so this
  is a record gap, not a vulnerable artifact.
- Fix: add a "Through springdoc 3.1.1" row for `org.apache.commons:commons-lang3` 3.20.0 (2025-11-12, Apache-2.0).

## Checked and clean

- Chain isolation: `apiDocsSecurity` (`SecurityConfig.java:70-79`) is `@Order(3)` after `actuatorSecurity` (1) and
  `internalSecurity` (2), so `/internal/**` and actuator keep their own chains. It matches only `/v3/api-docs`,
  `/v3/api-docs/**`, `/swagger-ui.html`, `/swagger-ui/**`. `apiSecurity` (now 4) is unchanged apart from its order.
- Path tricks: Spring Security 7.1.1 builds `securityMatcher(String...)` as `PathPatternRequestMatcher`, the same
  parser MVC uses (case-sensitive both sides, no trailing slash match). The default `StrictHttpFirewall` rejects `..`,
  `%2e`, `%2f`, `;`, `//` and `\` before chain selection, and Tomcat rejects `%2F` too. So `/v3/api-docs/../api/...`,
  `/swagger-ui/..;/api/...` and encoded forms answer 400 and never reach an `/api` or `/internal` handler. No
  `HttpFirewall`, `WebSecurityCustomizer`, servlet path or context path is set. `/swagger-ui.html` redirects (302); it
  does not forward. `OpenApiPrototypeTest` proves `/api/v1/projects` still answers 401 with the doc chain present.
- Production: `apiDocsSecurity` and `OpenApiConfig` are `@Profile("prototype")`. `application.yml:136-142` sets both
  switches to false. The guard refuses `true` in any relaxed spelling, including `SPRINGDOC_APIDOCS_ENABLED` and
  `SPRINGDOC_SWAGGERUI_ENABLED`, and also refuses them when absent (tests at `ProfileGuardTest`, 33 passed).
  `ProductionStartupTest` asserts no `OpenApiWebMvcResource` and no `SwaggerWelcomeWebMvc` bean and no doc path mapped.
  Even with springdoc on, production's last chain denies the paths. The gap is L1.
- springdoc defines no `SecurityFilterChain`, filter or method security. `SpringDocSecurityConfiguration` only reads
  existing beans to document a login endpoint, which is off by default.
- Description contents: `springdoc.paths-to-match: /api/v1/**` (`application-prototype.yml:16`). Every mapped path
  outside it (`/internal/v1/access-checks`, `/internal/v1/generation-requests/.../codegen-input`, `/error`) is excluded.
  The test asserts every path starts with `/api/v1/` and the body has no `/internal/`. `springdoc.show-actuator` and
  `use-management-port` keep their false defaults. The info text and the scheme description hold no URL, token, host
  or stack trace. The JSON is not wrapped in the envelope (test). No `x-user-id` header is described: no controller
  takes it.
- Swagger UI: `queryConfigEnabled` is unset, so the bundle default `false` holds and `?url=` and `?configUrl=` cannot
  load a remote spec. `validatorUrl` is sent as `""`, so no call goes to validator.swagger.io. `persistAuthorization` is
  false (no token in localStorage). Assets come from the webjar, not a CDN. Spring Security's default headers
  (`X-Frame-Options: DENY`, `nosniff`, `Cache-Control: no-store`) apply on the doc chain. swagger-ui 5.32.14 has no OSV
  advisory (Maven webjar and npm package).
- Tokens: the doc chain has no resource server, so a stray bearer is never read (test sends a garbage one: 200). No
  logging was added. The dev-token and Keycloak paths (`apiSecurity`, `DevTokenIntrospector`, `JwtDecoderConfig`) are
  unchanged. No token value appears in the diff, README or tests. The README names `PROJECT_DEV_TOKENS_FILE` only.
- Message converters: Jackson 2 on the classpath adds no HTTP converter. Spring Web 7.0.9 picks Jackson 3 for JSON and
  YAML (`tools.jackson.dataformat:jackson-dataformat-yaml` 3.1.7 was already present), so the user API accepts no
  new media type.
- Dependencies: every new artifact is Apache-2.0 or MIT and was published at least 14 days earlier (Maven Central
  Last-Modified: springdoc 3.1.1 2026-09-06, swagger-core 2.2.55 2026-08-31, swagger-ui 5.32.14 2026-08-19,
  webjars-locator-lite 1.1.4 2026-07-15, Jackson 2.21.7 2026-09-21). They are pinned in `libs.versions.toml` and
  `gradle.lockfile`, have sha256 entries in `gradle/verification-metadata.xml`, and the `<configuration>` block is
  unchanged. `scripts/osv-scan.sh` is clean. The Jackson 2 override clears the 2.21.5 advisories. The metadata also
  gained BOM-only poms (Spring Cloud 5.0.0-M1 and others from springdoc's parent) and Windows `protoc` executables;
  none is on a runtime classpath. `jakarta.xml.bind-api` was already on the runtime classpath. The gap is L3.
- Secrets and profiles: no secret value, `.env` or token file in the diff. No change to `ProfileGuard`'s other checks,
  migrator handling or stand-in rules. No database, grant or `/internal` change.

Verdict: approved. No high or medium finding; L1, L2 and L3 are low. They fit the one fix round, or else become backlog
items with TR-40.
