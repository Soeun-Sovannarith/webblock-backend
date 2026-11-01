# Security Guardrails & Hardening Rules

These rules ensure enterprise-grade security across the WebBlock AI Code Service.

## 1. File System & Path Traversal Protections

Any file modification proposed by an LLM or user must undergo strict sanitization in `app/services/glm.py` and `app/services/build_runner.py`:

1. **Strict Canonical Path Normalization**:
   - Normalize with `posixpath.normpath(path.replace('\\', '/'))`.
   - Explicitly reject null bytes (`\0`).
   - Explicitly reject Windows drive letters (`C:\`, `D:\`) and leading slashes (`/`).
   - Reject any path containing directory traversal elements (`..`).
2. **Path Whitelist**:
   - Every file must match regex: `^(app|components|lib|db)/[A-Za-z0-9_\-./]+\.(tsx|ts|css)$`.
3. **Workspace Boundary Enforcement**:
   - In `write_workspace()`, verify `target.resolve().is_relative_to(base.resolve())`. Fail immediately if a write targets outside the sandbox directory.
4. **Harness-Owned Immutability**:
   - Guard core templates against overwrite: `package.json`, `components/registry.tsx`, `components/blocks/theme.ts`, `components/blocks/theme.css`, `app/[[...slug]]/page.tsx`, `db/schema.ts`, `db/seed.sql`, the deleted `drizzle.config.ts` and `db/seed.mjs` (`scaffold.REMOVED_TEMPLATES`), and the fullstack files (`lib/db.ts`, `lib/products.ts`, `lib/business.ts`, `lib/portal-auth.ts`, `app/portal/**`, `portal/**`, `app/media/**`). Also reject a `.ts`/`.tsx` sibling of a harness-owned module, which the bundler would resolve first (`glm.HARNESS_MODULES`).

5. **Product Portal Single Sign-On**:
   - Each site is its own Keycloak client (`webblock-site-<projectId>`, `app/services/portal_sso.py`) and admits only users with its `owner` role. The site's client secret travels only in the deploy webhook's `site_env`; never log or store it.
   - `lib/portal-auth.ts` in the site templates verifies every ID and logout token (signature, issuer, audience, expiry, nonce, state, PKCE) and fails closed. See README section 9.

6. **Generated Site Data (`site_data`)**:
   - Every site's tables live in the shared `site_data` schema of this service's database (`sql/006_site_data.sql`). `FORCE`d row-level security (`tenant_isolation`) admits only the rows of the project whose token the connection sent (`webblock.site_token`). Never weaken a policy, grant `webblock_site` anything on `site_data.tenants`, or give it DDL.
   - The tenant token is an HMAC of the project id under `SITE_DATA_SECRET` (`app/services/site_data.py`); only its SHA-256 is stored. `SITE_DB_PASSWORD` (as `PGPASSWORD`) and the token travel only in `site_env`, and `SITE_DATA_SECRET` never leaves this service; never log any of them.

## 2. Docker Execution Sandboxing

When compiling and testing Next.js code (`npm run build` in `app/services/build_runner.py`):

1. **Capabilities Dropped**:
   - Must specify `--cap-drop ALL` or `cap_drop=['ALL']` in Docker SDK.
2. **Privilege Escalation Blocked**:
   - Must specify `--security-opt no-new-privileges:true`.
3. **Resource Quotas**:
   - Memory limit: 2GB (`--memory 2048m`).
   - CPU quota: 2.0 (`--cpus 2.0`).
   - PID limit: 512 (`--pids-limit 512`) to prevent fork bombs.
4. **Network Lockdown**:
   - The `INSTALL` phase runs with network for dependency resolution (`--ignore-scripts`).
   - The `BUILD` phase runs in complete network isolation: `--network none` (prevents SSR data exfiltration).
5. **Non-Root Execution**:
   - Container executes under host UID/GID (`user=f"{os.getuid()}:{os.getgid()}"`).

## 3. Input Sanitization & Prompt Injection Defense

1. **Control Character Stripping**:
   - In `app/schemas/business.py`, all user-supplied business strings (`business_name`, `business_description`, `address`) are scrubbed of non-printable ASCII control characters (`[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]`).
2. **TypeScript Literal Escaping**:
   - In `app/services/drizzle_generator.py`, all user values inserted into TypeScript schema files must be safely escaped (`\`, `"`, `\n`) to prevent arbitrary code execution or template breakouts.
3. **Pydantic Validation**:
   - All external API inputs must strictly validate types, string length bounds, and formats (e.g., regex email validation and 6-digit hex color format).
