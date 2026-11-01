# Sample website, variant "never-ready": the image builds and pushes like the site variant, but its
# container exits with status 1 at once, so the Deployment never becomes ready (crash loop). Used to
# prove that a candidate that never becomes ready fails the deploy and leaves the live site untouched.
FROM docker.io/nginxinc/nginx-unprivileged:1.31.5-alpine@sha256:19c132c9ab02d3b783f478743dafc7a7f42e27aa7d2bdcbec1bb1128ca8f2a07
COPY --chmod=0644 index.html /usr/share/nginx/html/index.html
EXPOSE 8080
ENTRYPOINT ["/bin/sh", "-c", "echo 'never-ready sample: exiting at once' >&2; exit 1"]
