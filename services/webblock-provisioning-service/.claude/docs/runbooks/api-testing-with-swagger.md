# Testing the API by hand with Swagger UI

Task SW (decision `0006` section 7, "Amended by task SW"). Swagger UI is built into the API and exists **only when
`PROVISIONING_PROFILE=prototype`**. In the production profile `/docs` and `/openapi.yaml` do not exist (they answer like
any unknown route: 401 without a token, then 404).

## Start the stack

```sh
make proto-up proto-run
```

The public listener is `http://127.0.0.1:18080`. `GET /docs` (the page) and `GET /openapi.yaml` (the hand-written
contract, byte for byte) need no token. The page loads Swagger UI 5.33.0 from `cdn.jsdelivr.net` with integrity hashes,
so your browser needs internet access. Every call you make from the page still needs the bearer token.

## Get the development token

The prototype's development tokens are in `bin/kind-state/proto/provisioning/tokens.json` (one entry per token with its
subject and scopes). Copy the `token` of an entry that has the scopes you need. Never paste it into logs, chats or
commits.

## Make something to deploy

Follow "Driving it by hand" in `.claude/docs/runbooks/prototype-demo.md`: create a sample commit
(`scripts/prototype/sample-repo.sh`), register the repository in `repositories.json`, and publish it with the simulator
(`python -m provisioning.hosts.devtools.simulate source-published ...`). You need the project ID (a UUID) you used.

## Call the API from the page

1. Open `http://127.0.0.1:18080/docs`.
2. Click **Authorize**, paste the token (no `Bearer ` prefix), click Authorize, close.
3. `GET /v1/projects/{project_id}/releases`: **Try it out**, enter the project ID, Execute. Repeat until the release is
   `built` (a build takes a few minutes). Note the `release_id`.
4. `POST .../releases/{release_id}/deploy`: enter both IDs and an `Idempotency-Key` (any new string, for example
   `deploy-1`). Expect `202`. Poll `GET /v1/projects/{project_id}/deployment` until the candidate is ready.
5. `POST .../releases/{release_id}/promote` with a new `Idempotency-Key` (`promote-1`). Expect `202`.
6. `GET /v1/projects/{project_id}/deployment` shows the live release. Open the site as described in the demo runbook
   (`Host: <project>.sites.webblock.test` at the Gateway).

Repeating a call with the same `Idempotency-Key` returns the original operation; a different request with the same key
is a conflict. A 401 means the token is missing or wrong; a 404 on a project can mean the token has no access to it.
