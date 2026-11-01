# Juniper & Thread, a generated WebBlock website: Next.js standalone server on port 8080.
#
# Runtime contract (what the Provisioning worker deploys):
#   - runs as UID/GID 65532, never root;
#   - works with a read-only root file system: the only writable path is /tmp (an emptyDir in the
#     cluster), and Next.js's cache directory points there;
#   - GET /healthz answers 200 without touching the database;
#   - the catalog connection comes from PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD and PGSSLMODE
#     (the Secret catalog-runtime), read at request time, never at build time.
#
# The build needs network access to registry.npmjs.org for `npm ci`. Install scripts are not run.
# Node.js 24 LTS, Alpine 3.24, pinned by index digest (24.21.0-alpine3.24, published 18 September 2026).
ARG NODE_IMAGE=docker.io/library/node:24.21.0-alpine3.24@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1

FROM ${NODE_IMAGE} AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund

FROM ${NODE_IMAGE} AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM ${NODE_IMAGE} AS runtime
# The runtime needs only node: remove npm, npx and yarn so they are not shipped.
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx \
      /usr/local/bin/corepack /usr/local/lib/node_modules/corepack /opt/yarn-* /usr/local/bin/yarn /usr/local/bin/yarnpkg
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=8080 \
    HOSTNAME=0.0.0.0
# Files stay owned by root and read-only to the server; only /tmp is written at run time.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
RUN ln -s /tmp/next-cache /app/.next/cache
USER 65532:65532
EXPOSE 8080
CMD ["node", "server.js"]
