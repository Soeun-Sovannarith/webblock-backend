# shellcheck shell=bash
# shellcheck disable=SC2034  # sourced pins: the variables are used by deploy/image/build.sh
# The base image of the Provisioning service's runtime image (task PK; decision 0016 section 9 ruling 4:
# python:3.14-slim-trixie, pinned by digest, at least 14 days old when pinned). Sourced by
# deploy/image/build.sh; read by scripts/py/age.py (make py-age), which checks BASE_IMAGE_PUBLISHED
# against the 14-day rule without a lookup. Change both lines together, after the check of
# .claude/docs/runbooks/prototype-in-cluster.md ("Moving the base image").
#
# Python 3.14.7 on Debian 13 (trixie), slim. The digest is the multi-platform index that the tag
# 3.14.7-slim-trixie names (Docker Hub, crane digest; read 15 October 2026). BASE_IMAGE_PUBLISHED is
# the tag's last update on Docker Hub, later than its linux/amd64 config's "created"
# (2026-09-19T01:03:01Z), so the later date counts. The 3.14.8 tags (2026-10-09) were younger than
# 14 days.
BASE_IMAGE="docker.io/library/python:3.14.7-slim-trixie@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d"
BASE_IMAGE_PUBLISHED="2026-09-25T20:08:39Z"
