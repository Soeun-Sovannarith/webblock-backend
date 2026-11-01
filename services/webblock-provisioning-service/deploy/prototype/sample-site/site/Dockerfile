# Sample website, variant "site": static files behind unprivileged nginx on port 8080 (UID 101).
# GET / answers 200 with the visible version string; /healthz and /readyz answer 200 (decision 0014
# section 15). Runs with a read-only root file system when /tmp is a writable volume. The base image
# is pinned by digest; BuildKit pulls it from the local registry mirror
# (deploy/prototype/buildkit/buildkitd.toml.tmpl) and falls back to Docker Hub.
FROM docker.io/nginxinc/nginx-unprivileged:1.31.5-alpine@sha256:19c132c9ab02d3b783f478743dafc7a7f42e27aa7d2bdcbec1bb1128ca8f2a07
COPY --chmod=0644 default.conf /etc/nginx/conf.d/default.conf
COPY --chmod=0644 index.html /usr/share/nginx/html/index.html
EXPOSE 8080
