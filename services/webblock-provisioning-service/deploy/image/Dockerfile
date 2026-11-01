# The Provisioning service's runtime image (task PK; decision 0016 section 9 rulings 4 and 5). One image
# for the API, the worker, the build worker, the bootstrap and the migration Job; each pod names its
# command (deploy/helm/provisioning). Built only by deploy/image/build.sh, from its allow-list context,
# with BASE_IMAGE from deploy/image/versions.sh. Legacy-builder syntax only (ruling 5's fallback): no
# RUN --mount, no COPY --chmod, no heredocs.
ARG BASE_IMAGE

FROM ${BASE_IMAGE} AS build
# The runtime dependencies of uv.lock only: no development group, not the project itself, every wheel
# checked against the lock's hash, no source build, no Python download. The network is used here and
# never at run time; neither uv nor pip reaches the final image.
ENV UV_NO_CONFIG=1 UV_NO_BUILD=1 UV_FROZEN=1 UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.14 UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 UV_CACHE_DIR=/tmp/uv-cache
COPY uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
# The vendored webblock-commons wheel (decision 0024 G1): the build needs no token or access to its
# repository. UV_FROZEN=1 installs the lock as it is, so each wheel is checked here against the
# SHA-256 uv.lock records for it (tests/unit/deploy/test_image_wheels.py keeps the two equal).
COPY vendor/ /build/vendor/
WORKDIR /build
RUN echo 'e1c494ef409790e3b238460b8f887097647c584a7fbc4e77a3adb47308e4a4f3  vendor/webblock_commons-0.1.0-py3-none-any.whl' | sha256sum -c -
RUN uv sync --no-dev --no-install-project && rm -rf /tmp/uv-cache
COPY src/ /app/src/
RUN /opt/venv/bin/python -m compileall -q --invalidation-mode unchecked-hash /app/src/provisioning

FROM ${BASE_IMAGE}
RUN rm -rf /usr/local/lib/python3.14/site-packages/pip /usr/local/lib/python3.14/site-packages/pip-* \
      /usr/local/lib/python3.14/ensurepip /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14
COPY --from=build /opt/venv /opt/venv
COPY --from=build /app/src /app/src
COPY goose /usr/local/bin/goose
# PYTHONPATH, not a wheel: the API reads src/contracts beside the package (hosts/api/wiring.py).
ENV PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin PYTHONPATH=/app/src PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 HOME=/tmp
WORKDIR /app
USER 10001:10001
