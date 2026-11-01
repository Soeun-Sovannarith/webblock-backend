# Juniper & Thread: the demo clothing shop

A real, working clothing shop of the kind a WebBlock customer could have generated. WebBlock Provisioning builds it on Jenkins, deploys it to the local Kind cluster and promotes it, as the end-to-end test of the service (decision `0014` section 14). This folder is the website's whole source; `scripts/prototype/publish-demo-site.sh clothing-shop` publishes it to the local Git server as repository `clothing-shop`.

- `/`: the shop's name and tagline from `catalog.business_profiles`, category filters, and a responsive grid of product cards (picture, category, name, price, "View details").
- `/products/<product id>`: the product's pictures, description, price, and attributes (sizes, colors, material, fit, care), with a link back.
- `/healthz`: `200 ok`, without touching the database (process check for Kubernetes).
- `/readyz`: `200 ready` only when a catalog query as the site's own role succeeds within 2 s and sees exactly one business profile (connection, credentials and the role's mapping to an active website all work); otherwise `503 not ready`. The promotion probe uses it (decision `0014` section 15). Failures are logged by error class and driver code only.
- When the catalog is unreachable, every page shows a friendly "We'll be right back" page, rendered on the server; the server keeps running and recovers by itself when the catalog returns.

## Stack

Next.js 16 (App Router, server components, standalone output), React 19, TypeScript in strict mode, Drizzle ORM 0.45 over node-postgres (`pg`). Tests use Vitest and happy-dom. Exact versions are pinned in `package.json` and `package-lock.json`.

## Data access

- **Drizzle only.** `src/db/schema.ts` describes `catalog.business_profiles`, `catalog.categories`, `catalog.products` and `catalog.product_images` exactly as Notion "6 · Shared catalog" defines them; `tests/schema.test.ts` checks every column's name, type and nullability. All reads are in `src/lib/catalog.ts`, written with the query builder, so every value is a bound parameter. ESLint refuses `sql`, `sql.raw` and templated SQL, and `tests/data-access-rules.test.ts` refuses them too, along with direct driver queries.
- **Tenancy is the database's.** The shop logs in as its website's own role `site_<uuid>`, and row-level security returns only that website's rows. No query filters by website, and nothing reads `catalog.websites` (the site role cannot).
- **Published only.** Every product query requires `status = 'published'`; drafts and archived products are invisible, also by direct URL (404).
- **Connection.** `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` and `PGSSLMODE`, from the Secret `catalog-runtime`, read on the first request (not at build or start). Every key is required; a missing or invalid key is logged by name, never by value. The pool holds at most 2 connections, with a 3 s connect timeout and a 5 s statement timeout. `PGSSLMODE` takes libpq's values: `disable`, `allow` and `prefer` connect without TLS (node-postgres has no fallback), `require` encrypts without verifying, `verify-ca` and `verify-full` verify the server.
- **Prices** are `NUMERIC(12,2)` US dollars, as the design defines (there is no currency column), formatted as `$1,234.50`; a null price shows "Price on request".
- **Pictures.** A `storage_key` is `<website id>/products/<name>.svg`. The shop serves it as `/products/<name>.svg` from `public/products/`, its own original SVG illustrations; any other key shows a placeholder.

## Container

`Dockerfile`: three stages on `node:24.21.0-alpine3.24`, pinned by digest. `npm ci` runs without install scripts and needs access to `registry.npmjs.org`. The runtime stage carries only `node` (npm, npx, corepack and yarn are removed), runs as UID/GID 65532, listens on 8080, and needs only `/tmp` writable: Next.js's cache directory is a link to `/tmp/next-cache`. Run it the way the cluster does:

```sh
docker run --read-only --tmpfs /tmp --user 65532:65532 --cap-drop ALL -p 127.0.0.1:8080:8080 \
  -e PGHOST=... -e PGPORT=5432 -e PGDATABASE=... -e PGUSER=site_... -e PGPASSWORD=... -e PGSSLMODE=disable \
  <image>
```

It starts in about a second and uses about 50 MiB of memory at rest.

## Commands

```sh
npm ci                          # install exactly the locked versions
npm run typecheck               # tsc --noEmit, strict
npm run lint                    # ESLint (Next.js core-web-vitals and TypeScript rules), no warnings allowed
npm test                        # Vitest: price formatting, card rendering, attributes, picture keys,
                                # configuration, Drizzle schema against the design, seed file, data-access rules
npm run build                   # Next.js production build (standalone)
bash scripts/docker-check.sh    # builds the image and checks the runtime contract with Docker alone
```

`scripts/docker-check.sh [IMAGE]` checks, without a cluster or a database: UID 65532, a read-only root with only `/tmp` writable, `/healthz` 200, `/readyz` 503 while `/healthz` stays 200 with the catalog unreachable, the friendly page on `/` and on a product page with the catalog unreachable, 404 for an invalid product ID, pictures served, the server still running, the password absent from the logs, and the missing keys named when no settings are given.

## Checking against the catalog

Once the prototype stack's catalog (`webblock-catalog-db`, task H) runs:

1. Register a website and load the seed, as the `catalog_platform` stand-in:
   ```sh
   uv run python -m provisioning.hosts.devtools.catalog_platform register --project <id> --business-type clothing_shop
   uv run python -m provisioning.hosts.devtools.catalog_platform seed --project <id> --file deploy/prototype/demo-sites/clothing-shop/seed/seed.json
   ```
2. Create the site role and its credential with `access_setup` (Provisioning's bootstrap), or for a check by hand, a `site_<id without hyphens>` login role that is a member of `catalog_site`.
3. Run the image on the stack's Docker network with that role's settings (`PGHOST=webblock-catalog-db`, `PGPORT=5432`, `PGSSLMODE` as the Secret has it), then check that `/` lists the 12 published products (not the draft "Quilted Liner Vest") and that a "View details" page renders with its pictures, price and attributes.

`seed/README.md` describes the seed file field by field against the design's columns.
