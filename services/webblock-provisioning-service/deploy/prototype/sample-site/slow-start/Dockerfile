# Sample website, variant "slow-start" (task G, worker crash recovery): like "site", but nginx starts
# 20 seconds after the container, so a deploy stays in progress long enough to kill the worker during
# it. Then it serves "/" with its version, /healthz and /readyz like "site".
FROM docker.io/nginxinc/nginx-unprivileged:1.31.5-alpine@sha256:19c132c9ab02d3b783f478743dafc7a7f42e27aa7d2bdcbec1bb1128ca8f2a07
COPY --chmod=0644 default.conf /etc/nginx/conf.d/default.conf
COPY --chmod=0644 index.html /usr/share/nginx/html/index.html
EXPOSE 8080
ENTRYPOINT ["/bin/sh", "-c", "sleep 20; exec /docker-entrypoint.sh nginx -g 'daemon off;'"]
