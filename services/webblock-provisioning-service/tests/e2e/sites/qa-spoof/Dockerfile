# QA variant "qa-spoof" (task QA): like "site", but every response carries its own X-Webblock-Release
# header naming another (made-up) release. The Gateway's header must overwrite it.
FROM docker.io/nginxinc/nginx-unprivileged:1.31.5-alpine@sha256:19c132c9ab02d3b783f478743dafc7a7f42e27aa7d2bdcbec1bb1128ca8f2a07
COPY --chmod=0644 default.conf /etc/nginx/conf.d/default.conf
COPY --chmod=0644 index.html /usr/share/nginx/html/index.html
EXPOSE 8080
