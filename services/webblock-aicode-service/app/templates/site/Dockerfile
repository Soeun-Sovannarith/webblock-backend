# Owned by the WebBlock harness and refreshed on every run. The site's image, built by the platform (Provisioning's
# Jenkins) from the repository root for linux/amd64. Provisioning's runtime contract for generated sites:
#   - a Next.js standalone server on 0.0.0.0:8080, running as UID/GID 65532;
#   - a read-only root file system: only /tmp is writable, and Next.js's cache directory points there;
#   - GET /healthz answers 200 without touching the database; GET /readyz answers 200 only when the database does;
#   - no database and no secrets during the build: PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD, PGSSLMODE and the
#     other settings arrive as environment variables at run time.
# npm ci needs registry.npmjs.org; install scripts are not run. Node.js 22 on Alpine, pinned by index digest.
ARG NODE_IMAGE=docker.io/library/node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914

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
# The server needs only node: npm, npx, corepack and yarn are not shipped.
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
RUN ln -s /tmp/next-cache /app/.next/cache
USER 65532:65532
EXPOSE 8080
CMD ["node", "server.js"]
