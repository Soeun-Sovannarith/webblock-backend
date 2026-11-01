"""The per-site admission policies (task W6-K; decision 0014 section 21 ruling 1 as amended by
section 23; TR-35): ``webblock-site-binding``, ``-fence``, ``-image``, ``-route-shape``,
``-routed-release`` and ``webblock-tenant-writer`` (``deploy/prototype/policy``).

Each planted bad object is a server-side dry run by Kind's admin impersonating the worker: RBAC
allows the verb to the worker, so the refusal must come from the named policy. The good cases are
the renderer's own output (``provisioning.infrastructure.kubernetes.render``), built here by calling
it, never copied. Nothing a dry run judges is stored."""

import copy
import subprocess
from collections.abc import Callable, Iterator
from typing import Any
from uuid import uuid4

import pytest
from tests.integration.stack._stack import (
    ADMIN,
    BOOTSTRAP_USER,
    SITE_SUFFIX,
    WORKER,
    WORKER_USER,
    Kubectl,
    Tenant,
    command_number,
    poll,
    release_objects,
    site_route,
)

from provisioning.infrastructure.kubernetes.render import (
    LABEL_PROJECT_ID,
    render_preview_route,
)

POLICIES = (
    "webblock-site-binding",
    "webblock-site-fence",
    "webblock-site-image",
    "webblock-site-route-shape",
    "webblock-site-routed-release",
    "webblock-tenant-writer",
)
FENCE = "websites.webblock.internal/command-number"
PREVIEW_TOKEN = "stack-test-preview-token-0123456789abcdef"  # noqa: S105 - a test value
INTRUDERS = "webblock-stack-test:intruders"
INTRUDER = "webblock-stack-test-intruder"

type Manifest = dict[str, Any]


def refused_by(done: subprocess.CompletedProcess[str], policy: str) -> bool:
    return done.returncode != 0 and f"ValidatingAdmissionPolicy '{policy}'" in done.stderr


def assert_refused(done: subprocess.CompletedProcess[str], policy: str) -> None:
    assert done.returncode != 0, f"admitted, expected a refusal by {policy}"
    assert f"ValidatingAdmissionPolicy '{policy}'" in done.stderr, done.stderr
    assert "Forbidden" in done.stderr or "forbidden" in done.stderr, done.stderr


def assert_admitted(done: subprocess.CompletedProcess[str]) -> None:
    assert done.returncode == 0, done.stderr


# --- installation ---------------------------------------------------------------------------------


@pytest.mark.parametrize("policy", POLICIES)
def test_policy_is_installed_type_checked_and_denies(kubectl: Kubectl, policy: str) -> None:
    found = kubectl.get(ADMIN, "validatingadmissionpolicy", policy, "default")
    assert found, f"ValidatingAdmissionPolicy {policy} is not installed"
    assert found["spec"]["failurePolicy"] == "Fail"
    status = found.get("status", {})
    assert status.get("observedGeneration") == found["metadata"]["generation"], status
    assert not status.get("typeChecking", {}).get("expressionWarnings"), status
    binding = kubectl.get(ADMIN, "validatingadmissionpolicybinding", policy, "default")
    assert binding, f"ValidatingAdmissionPolicyBinding {policy} is not installed"
    assert binding["spec"]["policyName"] == policy
    assert binding["spec"]["validationActions"] == ["Deny"]


# --- the renderer's output is admitted ------------------------------------------------------------


def renderer_output(tenant: Tenant) -> list[Manifest]:
    number = command_number()
    release = uuid4()
    preview = f"{tenant.subdomain}-preview.{SITE_SUFFIX}"
    return [
        *release_objects(tenant.project, release, tenant.image, number),
        site_route(tenant.project, tenant.release, number),
        site_route(tenant.project, None, number),
        render_preview_route(
            tenant.project,
            tenant.release,
            preview,
            number,
            preview_secret=PREVIEW_TOKEN,
            site_domain=SITE_SUFFIX,
            subdomain=tenant.subdomain,
        ),
    ]


def test_the_renderers_objects_are_admitted_for_the_worker(
    kubectl: Kubectl, tenant: Tenant
) -> None:
    for manifest in renderer_output(tenant):
        done = kubectl.try_apply_as(WORKER_USER, manifest)
        assert done.returncode == 0, (manifest["kind"], manifest["metadata"]["name"], done.stderr)


# --- planted defects, one policy each -------------------------------------------------------------


def _deployment(tenant: Tenant) -> Manifest:
    return release_objects(tenant.project, uuid4(), tenant.image, command_number())[0]


def _service(tenant: Tenant) -> Manifest:
    return release_objects(tenant.project, uuid4(), tenant.image, command_number())[1]


def _route(tenant: Tenant) -> Manifest:
    return site_route(tenant.project, tenant.release, command_number())


def _set_project(manifest: Manifest, project: str) -> Manifest:
    manifest["metadata"]["labels"][LABEL_PROJECT_ID] = project
    return manifest


def _template_project(manifest: Manifest, project: str) -> Manifest:
    spec = manifest["spec"]
    spec["selector"]["matchLabels"][LABEL_PROJECT_ID] = project
    spec["template"]["metadata"]["labels"][LABEL_PROJECT_ID] = project
    return manifest


def _service_misnamed(manifest: Manifest) -> Manifest:
    manifest["metadata"]["name"] = f"rel-{uuid4()}"
    return manifest


def _fence(manifest: Manifest, value: str | None) -> Manifest:
    if value is None:
        del manifest["metadata"]["annotations"]
    else:
        manifest["metadata"]["annotations"][FENCE] = value
    return manifest


def _image(manifest: Manifest, image: str) -> Manifest:
    manifest["spec"]["template"]["spec"]["containers"][0]["image"] = image
    return manifest


def _extra_container(manifest: Manifest, image: str, *, init: bool) -> Manifest:
    pod = manifest["spec"]["template"]["spec"]
    extra = copy.deepcopy(pod["containers"][0])
    extra["name"] = "extra"
    extra["image"] = image
    del extra["readinessProbe"]
    del extra["ports"]
    pod.setdefault("initContainers" if init else "containers", []).append(extra)
    return manifest


def _renamed(manifest: Manifest, name: str) -> Manifest:
    manifest["metadata"]["name"] = name
    return manifest


def _route_spec(manifest: Manifest, change: Callable[[Manifest], Any]) -> Manifest:
    change(manifest["spec"])
    return manifest


def _rule(manifest: Manifest) -> Manifest:
    rule: Manifest = manifest["spec"]["rules"][0]
    return rule


def _backend(manifest: Manifest, **fields: Any) -> Manifest:
    backend: Manifest = _rule(manifest)["backendRefs"][0]
    backend.update(fields)
    return manifest


def _redirect_elsewhere(tenant: Tenant) -> Manifest:
    # Custom domains are gone (decision 0014 section 23), so the renderer no longer makes a
    # redirect route; plant a redirect to a foreign host on the site route itself.
    route = _route(tenant)
    rule = _rule(route)
    rule.pop("backendRefs", None)
    rule["filters"] = [
        {"type": "RequestRedirect", "requestRedirect": {"hostname": "elsewhere.test"}}
    ]
    return route


def _mirror(manifest: Manifest, namespace: str) -> Manifest:
    _rule(manifest)["filters"].append(
        {
            "type": "RequestMirror",
            "requestMirror": {
                "backendRef": {"name": "rel-mirror", "namespace": namespace, "port": 80}
            },
        }
    )
    return manifest


def _preview(tenant: Tenant) -> Manifest:
    """The renderer's preview route of ``tenant``, on its own preview host."""
    return render_preview_route(
        tenant.project,
        tenant.release,
        f"{tenant.subdomain}-preview.{SITE_SUFFIX}",
        command_number(),
        preview_secret=PREVIEW_TOKEN,
        site_domain=SITE_SUFFIX,
        subdomain=tenant.subdomain,
    )


def _preview_on_hostname(tenant: Tenant, hostname: str) -> Manifest:
    """The renderer refuses a preview route on any other host (security review S3), so the
    host is changed after rendering, as a worker bug or a stolen worker token would."""
    manifest = _preview(tenant)
    manifest["spec"]["hostnames"] = [hostname]
    return manifest


def _preview_on_site_hostname(tenant: Tenant) -> Manifest:
    return _preview_on_hostname(tenant, tenant.host)


def _preview_on_old_preview_dash_hostname(tenant: Tenant) -> Manifest:
    """The preview host of before decision 0023 G18b (Task 12 review I2)."""
    return _preview_on_hostname(tenant, f"preview-{tenant.subdomain}.{SITE_SUFFIX}")


def _preview_on_old_dotted_hostname(tenant: Tenant) -> Manifest:
    """FR-20 (PREVIEW1): the preview host of decision 0023 G18b is refused, never served."""
    return _preview_on_hostname(tenant, f"{tenant.subdomain}.preview.{SITE_SUFFIX}")


def _header_filter(manifest: Manifest, change: dict[str, Any]) -> Manifest:
    """Security review S1: a RequestHeaderModifier other than the preview's removal."""
    _rule(manifest).setdefault("filters", []).append(
        {"type": "RequestHeaderModifier", "requestHeaderModifier": change}
    )
    return manifest


def _preview_header_filter(tenant: Tenant, change: dict[str, Any]) -> Manifest:
    manifest = _preview(tenant)
    _rule(manifest)["filters"] = _rule(manifest)["filters"][:1]  # drop the renderer's removal
    return _header_filter(manifest, change)


def _preview_without_response_filter(tenant: Tenant) -> Manifest:
    """FR-52.1: the renderer's preview route with only its header removal."""
    manifest = _preview(tenant)
    _rule(manifest)["filters"] = _rule(manifest)["filters"][1:]
    return manifest


def _preview_without_removal(tenant: Tenant) -> Manifest:
    """Task 12 security re-review L1: the renderer's preview route without its header removal,
    as a worker bug or a stolen worker token could write it."""
    manifest = _preview(tenant)
    _rule(manifest)["filters"] = _rule(manifest)["filters"][:1]
    return manifest


def _preview_response_headers(tenant: Tenant, headers: list[dict[str, str]]) -> Manifest:
    """FR-52.1 (preview-host security review M1): the preview route's response filter with other
    headers than the release header and ``Cache-Control: private, no-store``."""
    manifest = _preview(tenant)
    _rule(manifest)["filters"][0] = {
        "type": "ResponseHeaderModifier",
        "responseHeaderModifier": {"set": headers},
    }
    return manifest


def _preview_release_header(tenant: Tenant) -> dict[str, str]:
    return {"name": "X-Webblock-Release", "value": str(tenant.release)}


def _preview_matches(tenant: Tenant, matches: list[dict[str, Any]]) -> Manifest:
    """Security review S4: the preview route with other matches than the renderer's."""
    manifest = _preview(tenant)
    _rule(manifest)["matches"] = matches
    return manifest


_TOKEN_SHAPED = "a" * 43


Case = tuple[str, Callable[[Tenant, Tenant], Manifest]]

CASES: dict[str, Case] = {
    # webblock-site-binding
    "deployment-labelled-for-another-project": (
        "webblock-site-binding",
        lambda t, o: _set_project(_deployment(t), str(o.project)),
    ),
    "deployment-pods-of-another-project": (
        "webblock-site-binding",
        lambda t, o: _template_project(_deployment(t), str(o.project)),
    ),
    "service-labelled-for-another-project": (
        "webblock-site-binding",
        lambda t, o: _set_project(_service(t), str(o.project)),
    ),
    "service-not-named-after-its-release": (
        "webblock-site-binding",
        lambda t, o: _service_misnamed(_service(t)),
    ),
    "route-labelled-for-another-project": (
        "webblock-site-binding",
        lambda t, o: _set_project(_route(t), str(o.project)),
    ),
    # webblock-site-fence
    "deployment-without-fence": ("webblock-site-fence", lambda t, o: _fence(_deployment(t), None)),
    "service-fence-zero": ("webblock-site-fence", lambda t, o: _fence(_service(t), "0")),
    "route-fence-leading-zero": ("webblock-site-fence", lambda t, o: _fence(_route(t), "0123")),
    "route-fence-not-a-number": ("webblock-site-fence", lambda t, o: _fence(_route(t), "12a")),
    "deployment-fence-above-2^53-1": (
        "webblock-site-fence",
        lambda t, o: _fence(_deployment(t), "9007199254740992"),
    ),
    "route-without-fence": ("webblock-site-fence", lambda t, o: _fence(_route(t), None)),
    # webblock-site-image
    "image-by-tag": (
        "webblock-site-image",
        lambda t, o: _image(_deployment(t), t.image.split("@")[0] + ":latest"),
    ),
    "image-from-another-registry": (
        "webblock-site-image",
        lambda t, o: _image(_deployment(t), "docker.io/library/nginx@" + t.image.split("@")[1]),
    ),
    "image-of-another-project": (
        "webblock-site-image",
        lambda t, o: _image(_deployment(t), o.image),
    ),
    "image-digest-too-short": (
        "webblock-site-image",
        lambda t, o: _image(_deployment(t), t.image[:-1]),
    ),
    "second-container-unpinned": (
        "webblock-site-image",
        lambda t, o: _extra_container(_deployment(t), "docker.io/library/busybox:1", init=False),
    ),
    "init-container-of-another-project": (
        "webblock-site-image",
        lambda t, o: _extra_container(_deployment(t), o.image, init=True),
    ),
    # webblock-site-route-shape
    "route-with-another-name": (
        "webblock-site-route-shape",
        lambda t, o: _renamed(_route(t), f"extra-{t.project}"),
    ),
    "route-to-a-listenerset": (
        "webblock-site-route-shape",
        lambda t, o: _route_spec(
            _route(t),
            lambda s: s.update(
                parentRefs=[
                    {
                        "group": "gateway.networking.k8s.io",
                        "kind": "ListenerSet",
                        "name": f"ls-{t.project}",
                    }
                ]
            ),
        ),
    ),
    "route-to-another-gateway": (
        "webblock-site-route-shape",
        lambda t, o: _route_spec(_route(t), lambda s: s["parentRefs"][0].update(name="other")),
    ),
    "route-for-another-projects-subdomain": (
        "webblock-site-route-shape",
        lambda t, o: _route_spec(_route(t), lambda s: s.update(hostnames=[o.host])),
    ),
    "route-for-the-project-id-host": (
        # The host of before decision 0023 G18: no longer the site's own.
        "webblock-site-route-shape",
        lambda t, o: _route_spec(
            _route(t), lambda s: s.update(hostnames=[f"{t.project}.{SITE_SUFFIX}"])
        ),
    ),
    "route-for-the-wildcard": (
        "webblock-site-route-shape",
        lambda t, o: _route_spec(_route(t), lambda s: s.update(hostnames=[f"*.{SITE_SUFFIX}"])),
    ),
    "route-for-a-purchased-domain": (
        "webblock-site-route-shape",
        lambda t, o: _route_spec(_route(t), lambda s: s.update(hostnames=["shop-1.test"])),
    ),
    "route-attached-without-hostnames": (
        "webblock-site-route-shape",
        lambda t, o: _route_spec(_route(t), lambda s: s.pop("hostnames")),
    ),
    "backend-in-another-tenant": (
        "webblock-site-route-shape",
        lambda t, o: _backend(_route(t), namespace=o.namespace, name=o.service),
    ),
    "backend-not-a-release": (
        "webblock-site-route-shape",
        lambda t, o: _backend(_route(t), name="catalog"),
    ),
    "backend-with-its-own-filter": (
        "webblock-site-route-shape",
        lambda t, o: _backend(
            _route(t),
            filters=[
                {
                    "type": "RequestHeaderModifier",
                    "requestHeaderModifier": {"set": [{"name": "X-Extra", "value": "1"}]},
                }
            ],
        ),
    ),
    "rule-mirrors-traffic": (
        "webblock-site-route-shape",
        lambda t, o: _mirror(_route(t), t.namespace),
    ),
    "redirect-to-another-host": ("webblock-site-route-shape", lambda t, o: _redirect_elsewhere(t)),
    "preview-route-on-the-site-hostname": (
        "webblock-site-route-shape",
        lambda t, o: _preview_on_site_hostname(t),
    ),
    "preview-route-on-the-old-preview-dash-hostname": (
        "webblock-site-route-shape",
        lambda t, o: _preview_on_old_preview_dash_hostname(t),
    ),
    "fr20-preview-route-on-the-old-dotted-preview-hostname": (
        "webblock-site-route-shape",
        lambda t, o: _preview_on_old_dotted_hostname(t),
    ),
    # FR-52.1 (preview-host security review M1): a preview answer is never cacheable.
    "fr52-1-preview-route-without-no-store": (
        "webblock-site-route-shape",
        lambda t, o: _preview_response_headers(t, [_preview_release_header(t)]),
    ),
    "fr52-1-preview-route-with-a-cacheable-answer": (
        "webblock-site-route-shape",
        lambda t, o: _preview_response_headers(
            t, [_preview_release_header(t), {"name": "Cache-Control", "value": "public"}]
        ),
    ),
    "fr52-1-preview-route-sets-another-response-header": (
        "webblock-site-route-shape",
        lambda t, o: _preview_response_headers(
            t,
            [
                _preview_release_header(t),
                {"name": "Cache-Control", "value": "private, no-store"},
                {"name": "Set-Cookie", "value": "a=b"},
            ],
        ),
    ),
    "fr52-1-preview-route-without-a-response-filter": (
        "webblock-site-route-shape",
        lambda t, o: _preview_without_response_filter(t),
    ),
    # Decision 0023 G18c (Task 12 security review S1): only the preview route removes, and only
    # its own header; written, run in Task 9's Kind slot.
    "site-route-removes-the-preview-header": (
        "webblock-site-route-shape",
        lambda t, o: _header_filter(_route(t), {"remove": ["X-Webblock-Preview"]}),
    ),
    "preview-route-sets-a-request-header": (
        "webblock-site-route-shape",
        lambda t, o: _preview_header_filter(
            t, {"set": [{"name": "X-Webblock-Preview", "value": "x"}]}
        ),
    ),
    "preview-route-removes-another-header": (
        "webblock-site-route-shape",
        lambda t, o: _preview_header_filter(t, {"remove": ["Authorization"]}),
    ),
    # Task 12 security re-review L1: the preview route must remove its header; written, run in
    # Task 9's Kind slot.
    "preview-route-without-the-header-removal": (
        "webblock-site-route-shape",
        lambda t, o: _preview_without_removal(t),
    ),
    "preview-route-with-the-header-removal-twice": (
        "webblock-site-route-shape",
        lambda t, o: _header_filter(_preview(t), {"remove": ["X-Webblock-Preview"]}),
    ),
    # Security review S4: the preview route matches only with its header; written, run in Task 9.
    "preview-route-without-the-header-match": (
        "webblock-site-route-shape",
        lambda t, o: _preview_matches(t, [{"path": {"type": "PathPrefix", "value": "/"}}]),
    ),
    "preview-route-with-a-regex-header-match": (
        "webblock-site-route-shape",
        lambda t, o: _preview_matches(
            t,
            [
                {
                    "headers": [
                        {"type": "RegularExpression", "name": "X-Webblock-Preview", "value": ".*"}
                    ]
                }
            ],
        ),
    ),
    "preview-route-matching-another-header": (
        "webblock-site-route-shape",
        lambda t, o: _preview_matches(
            t, [{"headers": [{"type": "Exact", "name": "X-Other", "value": _TOKEN_SHAPED}]}]
        ),
    ),
    "preview-route-with-a-second-match-without-the-header": (
        "webblock-site-route-shape",
        lambda t, o: _preview_matches(
            t,
            [
                _rule(_preview(t))["matches"][0],
                {"path": {"type": "PathPrefix", "value": "/"}},
            ],
        ),
    ),
    "preview-route-with-a-short-header-value": (
        "webblock-site-route-shape",
        lambda t, o: _preview_matches(
            t, [{"headers": [{"type": "Exact", "name": "X-Webblock-Preview", "value": "x"}]}]
        ),
    ),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_planted_bad_object_is_refused_for_the_worker(
    kubectl: Kubectl, tenant: Tenant, other_tenant: Tenant, case: str
) -> None:
    policy, build = CASES[case]
    assert_refused(kubectl.try_apply_as(WORKER_USER, build(tenant, other_tenant)), policy)


# --- the routed release ---------------------------------------------------------------------------


def _delete_as_worker(
    kubectl: Kubectl, tenant: Tenant, kind: str, name: str
) -> subprocess.CompletedProcess[str]:
    return kubectl.run(
        ADMIN, "--as", WORKER_USER, "-n", tenant.namespace, "delete", kind, name, "--dry-run=server"
    )


def test_the_worker_cannot_delete_the_routed_release_but_can_delete_another(
    kubectl: Kubectl, tenant: Tenant
) -> None:
    kubectl.apply(WORKER, site_route(tenant.project, tenant.release, command_number()))
    routed = tenant.service

    def refused() -> bool | None:
        done = _delete_as_worker(kubectl, tenant, "deployment", routed)
        return True if refused_by(done, "webblock-site-routed-release") else None

    poll("the routed release's Deployment protected", 30, refused)
    for kind in ("deployment", "service"):
        assert_refused(
            _delete_as_worker(kubectl, tenant, kind, routed), "webblock-site-routed-release"
        )

    # Another release, not routed: the worker's token deletes it for real (ruling 8).
    spare = uuid4()
    for manifest in release_objects(tenant.project, spare, tenant.image, command_number()):
        kubectl.apply(WORKER, manifest)
    for kind in ("deployment", "service"):
        assert_admitted(_delete_as_worker(kubectl, tenant, kind, f"rel-{spare}"))
        kubectl.ok(WORKER, "-n", tenant.namespace, "delete", kind, f"rel-{spare}")

    # Once the route points at the offline page, the former release is no longer protected.
    kubectl.apply(WORKER, site_route(tenant.project, None, command_number()))
    try:

        def admitted() -> bool | None:
            done = _delete_as_worker(kubectl, tenant, "deployment", routed)
            return True if done.returncode == 0 else None

        poll("the unrouted release's Deployment deletable", 30, admitted)
        assert_admitted(_delete_as_worker(kubectl, tenant, "service", routed))
    finally:
        kubectl.apply(WORKER, site_route(tenant.project, tenant.release, command_number()))


# --- who writes -----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def intruder(kubectl: Kubectl) -> Iterator[str]:
    """A group bound to cluster-admin for this module: RBAC allows it everything, and it is not a
    break-glass group, so only webblock-tenant-writer can refuse it."""
    name = "webblock-stack-test-intruders"
    kubectl.apply(
        ADMIN,
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRoleBinding",
            "metadata": {"name": name},
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": "cluster-admin",
            },
            "subjects": [
                {"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": INTRUDERS}
            ],
        },
    )
    try:
        yield INTRUDERS
    finally:
        kubectl.run(ADMIN, "delete", "clusterrolebinding", name)


def _secret(namespace: str, name: str) -> Manifest:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": namespace},
        "type": "Opaque",
        "data": {"PGHOST": "aG9zdA=="},
    }


def _role_binding(namespace: str) -> Manifest:
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": {"name": "intruder", "namespace": namespace},
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "ClusterRole",
            "name": "webblock-tenant-worker",
        },
        "subjects": [{"kind": "ServiceAccount", "name": "default", "namespace": namespace}],
    }


def _relabel(tenant: Tenant) -> Manifest:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": tenant.namespace,
            "labels": {"pod-security.kubernetes.io/enforce": "privileged"},
        },
    }


WRITER_CASES: dict[str, Callable[[Tenant], Manifest]] = {
    "deployment": lambda t: _deployment(t),
    "service": lambda t: _service(t),
    "route": lambda t: _route(t),
    "rolebinding": lambda t: _role_binding(t.namespace),
    "namespace-labels": _relabel,
    "catalog-runtime": lambda t: _secret(t.namespace, "catalog-runtime"),
    "other-secret": lambda t: _secret(t.namespace, "intruder"),
}


@pytest.mark.parametrize("case", sorted(WRITER_CASES))
def test_only_the_owning_identity_writes_in_a_tenant(
    kubectl: Kubectl, tenant: Tenant, intruder: str, case: str
) -> None:
    manifest = WRITER_CASES[case](tenant)
    assert_refused(
        kubectl.try_apply_as(INTRUDER, manifest, intruder, force=True), "webblock-tenant-writer"
    )


@pytest.mark.parametrize("kind", ["deployment", "service", "httproute"])
def test_nobody_but_the_worker_deletes_a_per_site_object(
    kubectl: Kubectl, tenant: Tenant, intruder: str, kind: str
) -> None:
    name = f"route-{tenant.project}" if kind == "httproute" else tenant.service
    done = kubectl.run(
        ADMIN,
        "--as",
        INTRUDER,
        f"--as-group={intruder}",
        "-n",
        tenant.namespace,
        "delete",
        kind,
        name,
        "--dry-run=server",
    )
    assert_refused(done, "webblock-tenant-writer")


def test_the_worker_cannot_relabel_its_tenant_even_with_rbac(
    kubectl: Kubectl, tenant: Tenant, intruder: str
) -> None:
    """The worker identity with every RBAC grant still may not write the tenant's namespace or its
    RoleBindings: those are bootstrap's."""
    assert_refused(
        kubectl.try_apply_as(WORKER_USER, _relabel(tenant), intruder, force=True),
        "webblock-tenant-writer",
    )
    assert_refused(
        kubectl.try_apply_as(WORKER_USER, _role_binding(tenant.namespace), intruder, force=True),
        "webblock-tenant-writer",
    )


def test_bootstrap_cannot_write_a_release_even_with_rbac(
    kubectl: Kubectl, tenant: Tenant, intruder: str
) -> None:
    done = kubectl.try_apply_as(BOOTSTRAP_USER, _deployment(tenant), intruder, force=True)
    assert done.returncode != 0
    assert refused_by(done, "webblock-tenant-writer") or refused_by(
        done, "webblock-bootstrap-scope"
    ), done.stderr


def test_the_writer_policy_leaves_other_namespaces_alone(
    kubectl: Kubectl, tenant: Tenant, intruder: str, scratch_namespace: str
) -> None:
    deployment = _deployment(tenant)
    deployment["metadata"]["namespace"] = scratch_namespace
    assert_admitted(kubectl.try_apply_as(INTRUDER, deployment, intruder))
    assert_admitted(kubectl.try_apply_as(INTRUDER, _secret(scratch_namespace, "x"), intruder))
