# Runtime image scan (task H1; CLAUDE.md section 4), included by the Makefile with one line.
# .claude/docs/runbooks/python-p0.md explains it. Never reads .env.
#
#   image-scan   bin/grype, verified against its pin (scripts/py/tools.sh), scans the runtime images
#                against one verified offline Grype database per run (listing active, schema v6,
#                built at most 14 days ago, archive SHA-256 as listed, grype db status valid). It
#                builds each image named in IMAGE_SCAN_TARGETS (default sample-site; also
#                clothing-shop) with docker build, scans the saved archive and removes the image.
#                Any High or Critical match fails unless scripts/py/image-scan-exceptions.toml holds
#                a reviewed, time-bounded exception for it (scripts/py/image_scan.py).
#
# With the pinned docker-buildx plugin (make kind-tools; decision 0016 section 9 ruling 5) the local
# build uses BuildKit, which the sample site's COPY --chmod needs; without it, scan the image the
# stack's Jenkins and BuildKit built instead, by digest from the local registry (make proto-test-kind
# prints it as "proto-test-kind: site <ref>"; on the host the registry is localhost:5001):
#   make image-scan IMAGE_SCAN_REFS="sample-site=localhost:5001/webblock/sites/<id>@sha256:<digest>" \
#     IMAGE_SCAN_REGISTRY_CA=bin/kind-state/proto/registry-tls/ca.crt
# The service image is scanned by reference only, the one the cluster run pushed:
#   make image-scan IMAGE_SCAN_TARGETS=provisioning \
#     IMAGE_SCAN_REFS="provisioning=$(sed -n 's/^PROV_IMAGE_ON_HOST=//p' bin/kind-state/proto/provisioning/image.env)" \
#     IMAGE_SCAN_REGISTRY_CA=bin/kind-state/proto/registry-tls/ca.crt
#
# It builds or pulls images, so on the shared machine it runs only in a slot the lead gives.

.PHONY: image-scan

image-scan:
	@$(py_prelude) bash scripts/py/tools.sh grype && exec python3.14 -I scripts/py/image_scan.py
