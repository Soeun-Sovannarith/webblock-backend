# Test-only image for tests/integration/catalog (task H): a "site" that logs in to the catalog with the
# PG* variables the Deployment takes from the Secret catalog-runtime (envFrom), and answers on 8080 with
# what the catalog shows its role. Built locally and pushed to the stack's registry under
# webblock/sites/<projectId> by the test; never part of the service.
FROM docker.io/library/postgres:18.6-alpine3.24@sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873
# Both scripts are mode 0755 in the repository; COPY keeps the mode.
COPY probe.sh respond.sh /probe/
ENTRYPOINT ["/bin/sh", "/probe/probe.sh"]
CMD []
