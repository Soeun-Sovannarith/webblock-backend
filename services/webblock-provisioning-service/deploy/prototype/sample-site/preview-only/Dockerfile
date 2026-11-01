# Sample website, variant "preview-only" (task T2): like "site", but /readyz answers 200 only on a
# preview hostname, so a candidate passes its preview and fails on the site's hostname after the
# switch (decision 0014 section 17, the restore after a failed switch).
FROM docker.io/nginxinc/nginx-unprivileged:1.31.5-alpine@sha256:19c132c9ab02d3b783f478743dafc7a7f42e27aa7d2bdcbec1bb1128ca8f2a07
COPY --chmod=0644 default.conf /etc/nginx/conf.d/default.conf
COPY --chmod=0644 index.html /usr/share/nginx/html/index.html
EXPOSE 8080
