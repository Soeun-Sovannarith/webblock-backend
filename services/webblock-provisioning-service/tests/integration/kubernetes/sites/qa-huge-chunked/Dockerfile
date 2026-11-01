# Variant "qa-huge-chunked" (task W6-M, from QA's "qa-huge-readyz"): GET /readyz answers 200 with a
# 1 GiB body sent chunked, without Content-Length, so the promotion probe cannot refuse it from the
# header and must stop reading at its bound (QA-01).
FROM docker.io/nginxinc/nginx-unprivileged:1.31.5-alpine@sha256:19c132c9ab02d3b783f478743dafc7a7f42e27aa7d2bdcbec1bb1128ca8f2a07
USER root
RUN truncate -s 1G /usr/share/nginx/readyz.bin && chmod 0644 /usr/share/nginx/readyz.bin
USER 101
COPY --chmod=0644 default.conf /etc/nginx/conf.d/default.conf
COPY --chmod=0644 index.html /usr/share/nginx/html/index.html
EXPOSE 8080
