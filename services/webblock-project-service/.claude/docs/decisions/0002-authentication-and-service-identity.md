# 0002 Authentication and service identity

## Decision

- **Users.** A user is the `user_id` claim of a Keycloak access token (value: `account.users.id`). Realm roles are
  `USER` and `ADMIN`. Website ownership is not a role.
- **Where the token is checked.** The Istio ingress gateway checks the JWT, then the first service the request
  reaches checks it again itself. On `/api/**` the Project service checks the issuer, the audience `project`,
  `typ: Bearer`, the signature and `exp`, and that `user_id` is present (otherwise 403). It reads no scope or role.
- **Owner only.** Only the project's owner (`owner_user_id`) may use or manage it. Anyone else gets **404**, never 403.
- **No service-to-service tokens.** Every service runs in the Istio mesh with STRICT mTLS. A caller is its workload
  identity (its ServiceAccount). An Istio `AuthorizationPolicy` (caller and path) is the only gate on internal calls.
  Inner calls carry the `user_id` when there is a user, never a token. The callee trusts that `user_id` only from
  callers its policy allows, then checks permissions in its own data. Tokens are never logged and never sent outside.
- **Project's `/internal/**`** has a filter chain with no authentication. A bearer sent by mistake is ignored (never
  401). Method security annotations are banned in main code (ArchUnit). Calls to Payment carry no token.
- **Who may call Project** (its `AuthorizationPolicy`): `/api/*` from the ingress gateway and the web frontend;
  `POST /internal/v1/access-checks` from Provisioning's API and the tenant namespaces `wb-*`;
  `/internal/v1/generation-requests/*` from `ai-codegen`; `GET /internal/v1/projects/*/repository` from Provisioning's
  API and worker; the management port from Prometheus.
- **Prototype profile.** Dev tokens stand for users only; every entry needs a `userId`. Kind has no Istio yet, so
  `/internal/**` is open there.
- **Access check transport.** The target is the gRPC `AccessService` of the Notion "Web Block architecture" page.
  `POST /internal/v1/access-checks` (REST) is today's implementation and stays until it moves; its meaning is the same.

The Keycloak realm, clients, audiences and claims: `.claude/docs/contracts/authentication-contract.md`.

## Reason

Service tokens added secrets, token code and scope checks that the mesh already covers with mTLS identity. One rule
for every service (mesh identity inside, user JWT at the edge and at the first service) is simpler to build and review.
404 for a non-owner does not reveal that a project exists.

## Status

Accepted. Built in this service. Open outside it: Istio on Kind, Istio and Keycloak on the real cluster, Payment's
acceptance of "mesh identity, no bearer", and the shared auth library for inner calls.
