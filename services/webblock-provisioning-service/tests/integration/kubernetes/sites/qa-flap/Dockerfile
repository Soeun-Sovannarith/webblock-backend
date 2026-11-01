# QA variant "qa-flap" (task QA): like "site", but GET /readyz answers 200 about half of the time and
# 503 otherwise (decided by the millisecond of the request), like a site with an unstable dependency.
FROM docker.io/nginxinc/nginx-unprivileged:1.31.5-alpine@sha256:19c132c9ab02d3b783f478743dafc7a7f42e27aa7d2bdcbec1bb1128ca8f2a07
COPY --chmod=0644 default.conf /etc/nginx/conf.d/default.conf
COPY --chmod=0644 index.html /usr/share/nginx/html/index.html
EXPOSE 8080
