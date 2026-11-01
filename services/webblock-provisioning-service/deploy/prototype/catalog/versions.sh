# shellcheck shell=bash
# shellcheck disable=SC2034  # sourced pins: the variables are used by deploy/kind/proto-*.sh
# The shared catalog database of the prototype stack (decision 0014 section 14). Sourced by
# deploy/kind/proto-lib.sh; it runs nothing.

# PostgreSQL 18 (design revision 19 targets PostgreSQL 18). 18.6-alpine3.24, created 17 September
# 2026; the digest is the index digest the registry served for that tag (docker image inspect
# RepoDigests, 8 October 2026).
PROTO_CATALOG_IMAGE="docker.io/library/postgres:18.6-alpine3.24@sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873"

# Container webblock-catalog-db on the Docker network kind: pods reach it as webblock-catalog-db:5432,
# the host on 127.0.0.1 at a port Docker picks (stack.env, PROTO_CATALOG_PORT_ON_HOST).
PROTO_CATALOG_NAME="webblock-catalog-db"
PROTO_CATALOG_DATABASE="catalog"
PROTO_CATALOG_MEMORY="512m"

# Flyway CLI, which applies the Project service's catalog migrations (decision 0017 section 1) from
# $PROJECT_SERVICE_DIR/src/main/resources/db/migration/catalog: 12.4.0, the version the Project
# service's Spring Boot 4.1.1 BOM manages. 12.4.0-alpine, created 14 April 2026; the digest is the index
# digest `bin/crane digest flyway/flyway:12.4.0-alpine` returned on 14 October 2026.
PROTO_FLYWAY_IMAGE="flyway/flyway:12.4.0-alpine@sha256:b43c3d9b7227687682a9124451ac3dbc9b0003eca65290ad1dcda760345bc680"
PROTO_FLYWAY_MEMORY="512m"
