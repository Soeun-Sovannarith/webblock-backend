# 0019: Service identity is the mesh

**Sections 3 and 7 amended by decision [`0024`](0024-auth-and-telemetry-alignment.md) (G6, G7):** the user is the
`user_id` claim and the roles the top-level `roles` claim, both read by web-block-commons; `AUTH_SCOPE_CLAIM` and
`IDENTITY_USER_ID_CLAIM` are refused by name. A JWT without a valid `user_id` is refused 403 on every route, the
`ADMIN` path included, so "a principal without a platform user ID is no obstacle on this path" holds only for the
prototype's development tokens.

**Section 7 extended by decision [`0023`](0023-phase-c-rest-shape-and-admin.md) (G9, G10):** the admin suspend, resume,
redeploy and command-list actions join rollback on the `ADMIN` path, with no Project `CheckAccess` call.

Status: **decided by the owner (A1 to A9) and amended (D1 to D12), both approved**, and **amended again by the
owner (section 7: an `ADMIN` acts on any site, OD-45)** (the platform's authentication design, given in the Project
service's lead session). The source is the Project service's spec
`docs/superpowers/specs/2026-10-16-authentication-design.md` as amended on its branch `auth-docs` (`002bda5`):
decisions A1 to A9 and D1 to D12, sections 2 to 5 and 8, and the plan's task PR-3. This record is Provisioning's own
rule for it. It amends decision
[`0017`](0017-project-service-catalog-and-access.md) section 2 (the service token, the production refusal and the
settings).

## 1. No service-to-service token

- The platform runs an Istio **sidecar** mesh with STRICT mTLS. A workload's identity is its ServiceAccount.
- An internal call carries **no bearer token**. The caller is its mTLS identity, and an Istio `AuthorizationPolicy`
  on the callee (caller workload and path) is the only gate. Sidecars check no JWT.
- Provisioning's access check to the Project service therefore sends no `Authorization` header. The Project service's
  sidecar admits Provisioning's API ServiceAccount on `POST /internal/v1/access-checks`, and also the `wb-*` site
  namespaces (spec section 4.5; any site may ask about any project, spec section 8).
- The end user's token is still never relayed. This record changes nothing in the API's verifier.
- **Provisioning's own sidecar row** (amended spec section 4.5): the API admits `/v1/*` from `<frontend-sa>` **only**
  (D3: the Next.js server calls it inside the mesh with the user's bearer, D1; there is no public route and the ingress
  gateway is no longer a caller), `/mcp` from `<internal-gateway-sa>` **only** (D11: the team, through Cloudflare
  Access, the tunnel and the internal gateway), and metrics from `<prometheus-sa>`. Every other caller gets 403 from
  the sidecar.
- So **the API's own JWT check is the only one** on every request (amended spec section 5.2): neither the frontend's
  path nor the internal gateway checks a Keycloak JWT, and an MCP client must send a user's Keycloak bearer as well as
  pass Access. A2's two checks (gateway, then application) apply to the Project service's public `/api/**`, not to
  Provisioning.

## 2. What Provisioning changes

- `ProjectServiceAccess` (`infrastructure/project/access.py`) takes no token and sends no `Authorization` header. Its
  HTTP client keeps no cookies, so nothing one answer sets reaches a later check (PR-3 review M-1).
- `PROJECT_SERVICE_TOKEN_FILE` is removed. It is refused by name in every profile and mode, so a stale environment is
  caught at start-up; `scripts/prototype/run-provisioning.sh` stops the same way.
- **Production may use `PROJECT_ACCESS_MODE=project-service`**, with `PROJECT_SERVICE_URL` required. Production
  still refuses the stand-in modes and `PROJECT_ACCESS_FILE`, and refuses `PROJECT_SERVICE_URL` without the mode;
  without the mode it keeps the closed adapter (`hosts.shared.open_contracts`).
- In production `PROJECT_SERVICE_URL` is plain `http://` only when its host ends in `.svc` or `.svc.cluster.local`
  (any case, a trailing dot allowed), where the sidecar adds mTLS; any other host needs `https://`. For any scheme
  it refuses a host with any non-ASCII character (the HTTP client maps U+3002, U+FF0E and U+FF61 to `.`; an
  internationalised name is written in its `xn--` form; re-review R-1b). Also for any scheme
  it refuses, as the amended spec section 5.4 requires and the Project service's `ProfileGuard` does: loopback and
  unspecified addresses (`127.0.0.0/8`, `::1`, `0.0.0.0`, `::`, and their IPv4-mapped forms); `localhost` and names
  under it, `ip6-localhost`, `ip6-loopback` and `localhost.localdomain`; a host made only of decimal, octal or hex
  numbers that is not a canonical IPv4 literal (`127.1`, `2130706433`, `0x7f000001`); an IPv6 literal with a zone ID
  (`%`); and RFC 2606's `invalid` and `test` (as names or suffixes). No reason repeats the URL (PR-3 review M-2,
  re-review R-1).
- Production mutations stay refused (`hosts.shared.open_contracts`, OD-04 and the rest); with the mode, reads are
  decided by the Project service.

## 3. Roles

- The user is the `user_id` claim (Keycloak user attribute `account.users.id`). Keycloak puts two realm roles in a flat
  list claim `roles`: `USER` (every user, by default) and `ADMIN` (platform staff), case-sensitive.
- Provisioning in production: `AUTH_AUDIENCE=provisioning`, `AUTH_SCOPE_CLAIM=roles`,
  `IDENTITY_USER_ID_CLAIM=user_id`, `AUTH_ACTION_SCOPES` with `rollback_release=ADMIN` and `USER` for the other nine
  actions; the example is in `.claude/docs/runbooks/identity.md`, and a unit test loads it.
- Rollback is `ADMIN` only in code (FR-50): the gate refuses a caller without `ADMIN` whatever scope
  `AUTH_ACTION_SCOPES` maps `rollback_release` to, and production refuses a mapping of it, or of another admin-only
  action, to anything but `ADMIN` (`infrastructure/config/identity.py`).
- The gate checks the role, then the operation, then project access. The Project service answers for owners
  only, so an `ADMIN` who does not own the project would be denied (404) in project-service mode; section 7 gives
  `ADMIN` an operator path that skips the project check for `rollback_release`. Production refuses every mutation,
  rollback included, anyway.

## 4. Limits

- The Kind prototype has no Istio yet, so the Project service's `/internal/**` is open there (follow-up of the
  authentication plan). The mTLS gate is proven only on a cluster with the mesh (spec section 7 checks).
- The real cluster's Istio version, namespaces and ServiceAccounts are placeholders in the spec (section 4).

## 5. The owner's answers to the PR-3 re-review

- **Per-caller limits (re-review R-3).** No gateway sits in front of `/v1/*` any more (D3). Per-user limiting is done by
  Cloudflare (rate rules on `app.<domain>`, per IP) and by the Next.js server (per signed-in user). Provisioning keeps
  its global `HTTP_MAX_IN_FLIGHT` and its body limits. This amends `0006` section 7 and OD-55. `0013`
  section 9 A3 (slow-client limits at the gateway) is noted as affected.
- **Slow clients**. `/v1/*` has one caller,
  the trusted Next.js server, so protection against slow clients sits at the edge of `app.<domain>`: Cloudflare and
  the Istio ingress gateway's timeouts. Provisioning keeps its application-level timeouts and its body limits. This
  amends `0013` section 9 A3 for `/v1/*`.
- **The gateways (re-review R-4).** Istio everywhere: the Istio ingress gateway for the public hosts (the sites
  included) and `istio-internal-gateway` for the Cloudflare tunnel. Cilium stays only as the CNI. This amends `0009`
  section 2 and OD-35. Code and the Kind stack still use Cilium's Gateway (GatewayClass `cilium`); moving them,
  and re-checking what depended on Cilium's Gateway (the promotion probe's `ResponseHeaderModifier`, route status
  timing), is follow-up work.
- **The ops agent (re-review R-5).** The proposed in-cluster ops agent of `0015` gets its `AuthorizationPolicy` row on
  Provisioning's API (its ServiceAccount, on `/v1/*` and `/mcp`, still with a user's JWT) only when `0015` is accepted.
  Until then the API's sidecar refuses it (section 1).

## 6. Records

- `0017` section 2 is amended where it names the service token, the production refusal and the token setting.
- OD-01 (access part: production may use it), OD-05 (answered), OD-35 (Istio mode: sidecar; the gateways, section 5),
  OD-45 (`ADMIN` holds `rollback_release`; the operator path, section 7), OD-55 (section 5), OD-69 (closed):
  `open-decisions.md`.
- Amendment notes for section 5: `0006` section 7, `0009` section 2, `0013` section 9 A3, `0015`;
  the retired ARCHITECTURE.md sections 4 (diagram) and 13.1.
- Runbooks: `identity.md` (production example), `production-profile.md`, `prototype-stack.md`.

## 7. Amendment: an `ADMIN` acts on any site (answers OD-45's operator path)

**Rule (owner).**

- A platform admin may suspend, resume and roll back **any** site, whether or not they own it.
- For those actions Provisioning skips the Project service's owner check when the token holds the realm role `ADMIN`
  in the roles claim (`AUTH_SCOPE_CLAIM=roles`, section 3). Role names are case-sensitive (A5): `admin`, `Admin` or any
  other spelling is not `ADMIN`, and `USER` or any other role never skips the check.
- Every other action, and every caller without `ADMIN`, keeps the owner check (section 3's order).
- Because the reads keep the owner check, the `202`'s status URL
  (`get_command`) and `get_deployment` answer 404 for an `ADMIN` who does not own the project. That admin follows
  the command through the operator tools or the logs (the audit event). Admin reads are for the owner, with the admin
  dashboard (OD-45); the code is unchanged.
- Audit: the command row's `requested_by_subject` records the admin's subject, as for every rollback, and the audit
  event of the mutation names the same subject (`0014` section 21). There is no database change, and the revision 20
  design is not edited.
- When the owner check is skipped, the mutation's audit event has
  `actor_kind=operator` in place of `user` (REST) or `agent` (MCP), and still names the verified subject as the actor.
  So the event alone tells an admin override from an owner acting on their own site. This holds for every outcome after
  the skip, a refusal included. A caller refused before the skip (by the role check or the operation gate) keeps the
  transport's kind (`OperationGate.check` returns whether it skipped; `Auditor.run` in
  `core/application/operations/auditing.py`).
- Production still refuses every mutation (`hosts.shared.open_contracts`); enabling these mutations in production is a
  later step. The skip changes nothing about that refusal: the operation gate refuses before the project step.

**What it covers.** The admin-only actions: `rollback_release` (FR-50) and the four of decision `0023` G10
(`suspend_website`, `resume_website`, `redeploy_release`, `admin_list_commands`). `suspend_hosting` and
`resume_hosting` commands from Payment events (`0014` section 21) pass no gate.

**Where.** In the one gate every operation passes (`OperationGate.check`,
`core/application/operations/gate.py`): after the role check (step 1) and the operation gate (step 2), and after the nil
project ID is refused, an admin-only action (`Action.is_admin_only()`, `core/application/shared/action.py`) returns
without asking project access. Only an `ADMIN` reaches that step: step 1 refuses such an action to a principal whose
scopes lack `ADMIN_ROLE` (`"ADMIN"`), whatever scope `AUTH_ACTION_SCOPES` maps it to. The principal's scopes are the
roles claim the verifier read; a development token's scopes stand in for it in the prototype (`prototype-admin`). A
principal without a platform user ID is no obstacle on this path: the Project service is not asked, and the subject is
what the command records.

**The claim is pinned.** Because `ADMIN` skips the owner check,
the production profile refuses any `AUTH_SCOPE_CLAIM` other than `roles` (A5), with a fixed reason
(`infrastructure/config/identity.py`, `SCOPE_CLAIM_IN_PRODUCTION`). On the Keycloak side the `roles` claim must carry
realm roles only, and no service account may hold `ADMIN` (`.claude/docs/runbooks/identity.md`).
