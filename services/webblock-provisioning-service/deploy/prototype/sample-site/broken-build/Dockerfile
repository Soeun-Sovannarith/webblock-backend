# Sample website, variant "broken-build": the Dockerfile fails during the build, so the Jenkins job
# reports failure_code build_failed and pushes nothing.
FROM docker.io/nginxinc/nginx-unprivileged:1.31.5-alpine@sha256:19c132c9ab02d3b783f478743dafc7a7f42e27aa7d2bdcbec1bb1128ca8f2a07
COPY --chmod=0644 index.html /usr/share/nginx/html/index.html
RUN echo 'broken-build sample: this step fails on purpose' >&2 && exit 1
