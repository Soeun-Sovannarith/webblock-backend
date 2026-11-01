"""The client wrapper: where a client may come from, the request shape of server-side apply, and
the mapping of API answers to typed errors (decision 0013 section 9; review-stack.md H1, H2, M6)."""

import asyncio
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from provisioning.core.application.ports.site_cluster import (
    ApplyConflict,
    ClusterRefused,
    ClusterUnavailable,
)
from provisioning.infrastructure.kubernetes import (
    BOOTSTRAP_FIELD_MANAGER,
    WORKER_FIELD_MANAGER,
    KubeApi,
    KubeconfigRefused,
    KubernetesConflict,
    KubernetesForbidden,
    KubernetesNotFound,
    Resource,
    connect_kubeconfig,
)

if TYPE_CHECKING:
    from tests.unit.infrastructure.kubernetes.conftest import (
        FakeApiServer,
        Json,
        KubeconfigFactory,
        PoisonListener,
    )

TIMEOUT = 5.0
NS = "wb-0b7c2f43-5d5e-4f1e-9a43-0e1d5c1c7a11"
DEPLOYMENT_PATH = f"/apis/apps/v1/namespaces/{NS}/deployments/rel-x"
LEAK_MARKER = "s3cr3t-body-text-that-must-not-leak"


def deployment(name: str = "rel-x") -> Json:
    labels = {"app": name}
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": NS},
        "spec": {
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {"containers": [{"name": "site", "image": "r.test/x@sha256:" + "0" * 64}]},
            },
        },
    }


async def connect(path: Path, manager: str = WORKER_FIELD_MANAGER) -> KubeApi:
    return await connect_kubeconfig(path, field_manager=manager, request_timeout_seconds=TIMEOUT)


# --- where a client may come from ----------------------------------------------------------------


# A fresh interpreter whose HOME and KUBECONFIG are poisoned before anything is imported: the
# library fixes its default path at import, so only this proves the default is never used.
CHILD = """
import asyncio, sys
from pathlib import Path
from provisioning.infrastructure.kubernetes import WORKER_FIELD_MANAGER, Resource
from provisioning.infrastructure.kubernetes import connect_kubeconfig

async def main():
    api = await connect_kubeconfig(
        Path(sys.argv[1]), field_manager=WORKER_FIELD_MANAGER, request_timeout_seconds=5.0
    )
    try:
        print("READ", await api.read(Resource.DEPLOYMENT, "rel-x", sys.argv[2]))
    finally:
        await api.close()

asyncio.run(main())
"""


@pytest.mark.asyncio
async def test_poisoned_home_and_kubeconfig_are_never_read_or_written(
    fake: FakeApiServer, make_kubeconfig: KubeconfigFactory, tls_poison: PoisonListener
) -> None:
    # The poisoned files name a kind-webblock-* context on 127.0.0.1, so only the explicit path
    # keeps the client away from them: if the wrapper fell back to $KUBECONFIG or
    # ~/.kube/config, the poison listener would count a connection.
    explicit = make_kubeconfig(fake.url)
    watched = (*tls_poison.files, explicit)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in watched}
    home, env_kubeconfig = tls_poison.files[0].parent.parent, tls_poison.files[1]
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-c",
        CHILD,
        str(explicit),
        NS,
        env={"HOME": str(home), "KUBECONFIG": str(env_kubeconfig), "PATH": os.defpath},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    async with asyncio.timeout(60):
        out, err = await child.communicate()

    assert child.returncode == 0, err.decode()
    assert out.decode().strip() == "READ None"
    assert tls_poison.connections == 0
    assert [r.path for r in fake.seen("GET")] == [DEPLOYMENT_PATH]
    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in watched}
    assert after == before


def test_the_library_default_is_the_poisoned_path() -> None:
    # The folder's conftest poisons KUBECONFIG before the library is first imported in this
    # process, so a regression to the default path can only find a file that never exists.
    poisoned = Path(__file__).resolve().parent / "never-a-kubeconfig" / "poisoned-default"

    module = sys.modules["kubernetes_asyncio.config.kube_config"]
    assert module.KUBE_CONFIG_DEFAULT_LOCATION == str(poisoned)
    assert not poisoned.exists()


def test_the_wrapper_never_persists_a_kubeconfig() -> None:
    # Refreshing a credential is what writes a kubeconfig back, and the wrapper refuses the exec
    # and auth-provider users that refresh, so a regression of persist_config is not observable
    # through behaviour; the brief requires persist_config=False, so the source pins it.
    source = Path(_client_file()).read_text()
    assert source.count("persist_config=False,") == 1  # the one loader call
    assert "persist_config=True" not in source


def _client_file() -> str:
    import provisioning.infrastructure.kubernetes as package

    return str(Path(package.__file__).with_name("_client.py"))


@pytest.mark.parametrize(
    "context",
    ["kind-other", "webblock-unit", "kind-webblock-", "do-ams3-ksga", "kind-webblock-UPPER"],
)
@pytest.mark.asyncio
async def test_a_context_that_is_not_kind_webblock_is_refused(
    fake: FakeApiServer, make_kubeconfig: KubeconfigFactory, context: str
) -> None:
    with pytest.raises(KubeconfigRefused):
        await connect(make_kubeconfig(fake.url, context=context))
    assert fake.requests == []


@pytest.mark.asyncio
async def test_tr34_fr17_a_k3d_webblock_context_on_127_0_0_1_connects(
    fake: FakeApiServer, make_kubeconfig: KubeconfigFactory
) -> None:
    """TR-34, FR-17 (spike G3): the local k3d cluster's context ``k3d-webblock-proto`` on
    ``https://127.0.0.1:<port>`` is accepted like a Kind one, and the read reaches that server."""
    api = await connect(make_kubeconfig(fake.url, context="k3d-webblock-proto"))
    try:
        assert await api.read(Resource.DEPLOYMENT, "rel-x", NS) is None
    finally:
        await api.close()
    assert [r.path for r in fake.requests] == [DEPLOYMENT_PATH]


@pytest.mark.parametrize(
    "context",
    [
        "k3d-webblock-",
        "k3d-other",
        "k3d-webblock-UPPER",
        "k3s-webblock-proto",
        "K3D-webblock-proto",
        "k3d-kind-webblock-proto",
        "kind-k3d-webblock-proto",
    ],
)
@pytest.mark.asyncio
async def test_tr34_fr17_a_context_that_is_not_kind_or_k3d_webblock_is_refused(
    fake: FakeApiServer, make_kubeconfig: KubeconfigFactory, context: str
) -> None:
    """TR-34, FR-17 (spike G3): only ``kind-webblock-*`` and ``k3d-webblock-*`` contexts pass;
    no request reaches the server."""
    with pytest.raises(KubeconfigRefused, match="kind-webblock-\\* or k3d-webblock-\\*"):
        await connect(make_kubeconfig(fake.url, context=context))
    assert fake.requests == []


@pytest.mark.parametrize(
    "server",
    [
        "https://0.0.0.0:{port}",
        "https://localhost:{port}",
        "https://203.0.113.10:{port}",
    ],
)
@pytest.mark.asyncio
async def test_tr34_fr17_a_k3d_context_whose_server_is_not_127_0_0_1_is_refused(
    fake: FakeApiServer, make_kubeconfig: KubeconfigFactory, server: str
) -> None:
    """TR-34, FR-17 (spike G3): a ``k3d-webblock-proto`` context on k3d's default
    ``https://0.0.0.0:<port>``, on ``localhost`` or on a public address is refused before any
    request."""
    port = fake.url.rsplit(":", 1)[1]
    with pytest.raises(KubeconfigRefused, match=r"https://127\.0\.0\.1:<port>"):
        await connect(make_kubeconfig(server.format(port=port), context="k3d-webblock-proto"))
    assert fake.requests == []


@pytest.mark.parametrize(
    "server",
    [
        "https://localhost:{port}",
        "https://127.0.0.2:{port}",
        "http://127.0.0.1:{port}",
        "https://127.0.0.1",
        "https://127.0.0.1:{port}/prefix",
        "https://10.0.0.1:{port}",
        "https://ksga.k8s.ondigitalocean.com",
        "https://127.0.0.1:{port}@evil.example",
    ],
)
@pytest.mark.asyncio
async def test_a_server_that_is_not_https_127_0_0_1_port_is_refused(
    fake: FakeApiServer, make_kubeconfig: KubeconfigFactory, server: str
) -> None:
    port = fake.url.rsplit(":", 1)[1]
    with pytest.raises(KubeconfigRefused):
        await connect(make_kubeconfig(server.format(port=port)))
    assert fake.requests == []


@pytest.mark.asyncio
async def test_an_exec_or_auth_provider_user_is_refused_before_anything_runs(
    fake: FakeApiServer, make_kubeconfig: KubeconfigFactory, tmp_path: Path
) -> None:
    marker = tmp_path / "exec-ran"
    exec_user = {
        "exec": {
            "apiVersion": "client.authentication.k8s.io/v1",
            "command": "/bin/sh",
            "args": ["-c", f"touch {marker}"],
            "interactiveMode": "Never",
        }
    }
    with pytest.raises(KubeconfigRefused):
        await connect(make_kubeconfig(fake.url, user=exec_user))
    with pytest.raises(KubeconfigRefused):
        await connect(make_kubeconfig(fake.url, user={"auth-provider": {"name": "oidc"}}))
    assert not marker.exists()
    assert fake.requests == []


@pytest.mark.asyncio
async def test_a_proxy_in_the_kubeconfig_is_refused(
    fake: FakeApiServer, make_kubeconfig: KubeconfigFactory
) -> None:
    with pytest.raises(KubeconfigRefused):
        await connect(
            make_kubeconfig(fake.url, cluster_extra={"proxy-url": "http://10.0.0.1:3128"})
        )


@pytest.mark.asyncio
async def test_a_path_that_is_relative_missing_or_a_list_is_refused(
    fake: FakeApiServer, make_kubeconfig: KubeconfigFactory, tmp_path: Path
) -> None:
    good = make_kubeconfig(fake.url)
    relative = Path(os.path.relpath(good))  # noqa: ASYNC240 - string arithmetic on a test path
    with pytest.raises(KubeconfigRefused):
        await connect(relative)
    with pytest.raises(KubeconfigRefused):
        await connect(tmp_path / "does-not-exist")
    with pytest.raises(KubeconfigRefused):
        await connect(Path(f"{good}:{good}"))
    assert fake.requests == []


@pytest.mark.asyncio
async def test_an_unknown_field_manager_is_refused(
    fake: FakeApiServer, make_kubeconfig: KubeconfigFactory
) -> None:
    with pytest.raises(ValueError, match="field manager"):
        await connect(make_kubeconfig(fake.url), manager="")


# --- the request shape of server-side apply ------------------------------------------------------


@pytest.mark.parametrize("manager", [WORKER_FIELD_MANAGER, BOOTSTRAP_FIELD_MANAGER])
@pytest.mark.asyncio
async def test_apply_is_a_server_side_apply_patch_with_strict_validation_and_no_force(
    fake: FakeApiServer, make_kubeconfig: KubeconfigFactory, manager: str
) -> None:
    api = await connect(make_kubeconfig(fake.url), manager)
    try:
        applied = await api.apply(Resource.DEPLOYMENT, deployment())
    finally:
        await api.close()

    (patch,) = fake.seen("PATCH")
    assert patch.path == DEPLOYMENT_PATH
    assert patch.content_type == "application/apply-patch+yaml"
    assert patch.query == {
        "fieldManager": manager,
        "force": "false",
        "fieldValidation": "Strict",
    }
    assert patch.authorization == "Bearer fake-worker-token-for-unit-tests"
    assert patch.body == deployment()
    assert applied["metadata"]["name"] == "rel-x"
    assert applied["metadata"]["uid"]


@pytest.mark.parametrize(
    ("resource", "manifest", "path"),
    [
        (
            Resource.SERVICE,
            {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "s", "namespace": NS}},
            f"/api/v1/namespaces/{NS}/services/s",
        ),
        (
            Resource.HTTP_ROUTE,
            {
                "apiVersion": "gateway.networking.k8s.io/v1",
                "kind": "HTTPRoute",
                "metadata": {"name": "r", "namespace": NS},
            },
            f"/apis/gateway.networking.k8s.io/v1/namespaces/{NS}/httproutes/r",
        ),
        (
            Resource.NAMESPACE,
            {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NS}},
            f"/api/v1/namespaces/{NS}",
        ),
        (
            Resource.RESOURCE_QUOTA,
            {
                "apiVersion": "v1",
                "kind": "ResourceQuota",
                "metadata": {"name": "q", "namespace": NS},
            },
            f"/api/v1/namespaces/{NS}/resourcequotas/q",
        ),
        (
            Resource.LIMIT_RANGE,
            {"apiVersion": "v1", "kind": "LimitRange", "metadata": {"name": "l", "namespace": NS}},
            f"/api/v1/namespaces/{NS}/limitranges/l",
        ),
        (
            Resource.ROLE_BINDING,
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "RoleBinding",
                "metadata": {"name": "b", "namespace": NS},
                "roleRef": {
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "ClusterRole",
                    "name": "webblock-tenant-worker",
                },
            },
            f"/apis/rbac.authorization.k8s.io/v1/namespaces/{NS}/rolebindings/b",
        ),
        (
            Resource.REFERENCE_GRANT,
            {
                "apiVersion": "gateway.networking.k8s.io/v1beta1",
                "kind": "ReferenceGrant",
                "metadata": {"name": "g", "namespace": "webblock-offline"},
            },
            "/apis/gateway.networking.k8s.io/v1beta1/namespaces/webblock-offline/referencegrants/g",
        ),
    ],
)
@pytest.mark.asyncio
async def test_every_resource_is_applied_the_same_way(
    fake: FakeApiServer,
    worker_api: KubeApi,
    resource: Resource,
    manifest: Json,
    path: str,
) -> None:
    await worker_api.apply(resource, manifest)

    (patch,) = fake.seen("PATCH")
    assert patch.path == path
    assert patch.content_type == "application/apply-patch+yaml"
    assert patch.query == {
        "fieldManager": WORKER_FIELD_MANAGER,
        "force": "false",
        "fieldValidation": "Strict",
    }


@pytest.mark.asyncio
async def test_apply_refuses_a_manifest_that_does_not_match_the_resource(
    fake: FakeApiServer, worker_api: KubeApi
) -> None:
    service = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "s", "namespace": NS}}
    with pytest.raises(ValueError, match="kind"):
        await worker_api.apply(Resource.DEPLOYMENT, service)
    assert fake.requests == []


@pytest.mark.asyncio
async def test_delete_sends_uid_and_resource_version_preconditions(
    fake: FakeApiServer, worker_api: KubeApi
) -> None:
    fake.put(DEPLOYMENT_PATH, deployment())

    assert await worker_api.delete(
        Resource.DEPLOYMENT, "rel-x", NS, uid="uid-1", resource_version="42"
    )

    (delete,) = fake.seen("DELETE")
    assert delete.path == DEPLOYMENT_PATH
    assert delete.body["preconditions"] == {"uid": "uid-1", "resourceVersion": "42"}
    assert delete.body["propagationPolicy"] == "Background"


@pytest.mark.asyncio
async def test_delete_of_a_missing_object_reports_false(worker_api: KubeApi) -> None:
    assert not await worker_api.delete(
        Resource.DEPLOYMENT, "rel-x", NS, uid="uid-1", resource_version="42"
    )


@pytest.mark.asyncio
async def test_list_sends_the_label_selector(fake: FakeApiServer, worker_api: KubeApi) -> None:
    items = await worker_api.list_objects(Resource.POD, NS, "a=b,c=d")

    assert items == []
    (get,) = fake.seen("GET")
    assert get.path == f"/api/v1/namespaces/{NS}/pods"
    assert get.query["labelSelector"] == "a=b,c=d"


ROUTE = {
    "apiVersion": "gateway.networking.k8s.io/v1",
    "kind": "HTTPRoute",
    "metadata": {"name": "route-x", "namespace": NS},
}
ROUTE_PATH = f"/apis/gateway.networking.k8s.io/v1/namespaces/{NS}/httproutes/route-x"


@pytest.mark.asyncio
async def test_a_forced_apply_sends_force_true(fake: FakeApiServer, worker_api: KubeApi) -> None:
    """Decision 0014 section 23 item 3: the operator rollback's reclaim of the route."""
    await worker_api.apply(Resource.HTTP_ROUTE, ROUTE, force=True)

    (patch,) = fake.seen("PATCH")
    assert patch.path == ROUTE_PATH
    assert patch.query == {
        "fieldManager": WORKER_FIELD_MANAGER,
        "force": "true",
        "fieldValidation": "Strict",
    }


@pytest.mark.parametrize(
    "resource",
    [r for r in Resource if r is not Resource.HTTP_ROUTE],
)
@pytest.mark.asyncio
async def test_nothing_but_an_http_route_is_ever_force_applied(
    fake: FakeApiServer, worker_api: KubeApi, resource: Resource
) -> None:
    with pytest.raises(ValueError, match="force"):
        await worker_api.apply(resource, deployment(), force=True)
    assert fake.requests == []


@pytest.mark.asyncio
async def test_a_dry_run_apply_sends_dry_run_all_and_stores_nothing(
    fake: FakeApiServer, worker_api: KubeApi
) -> None:
    await worker_api.apply(Resource.DEPLOYMENT, deployment(), dry_run=True)

    (patch,) = fake.seen("PATCH")
    assert patch.query == {
        "fieldManager": WORKER_FIELD_MANAGER,
        "force": "false",
        "fieldValidation": "Strict",
        "dryRun": "All",
    }


@pytest.mark.parametrize(
    ("resource", "namespace", "path"),
    [
        (Resource.NAMESPACE, None, "/api/v1/namespaces"),
        (Resource.DEPLOYMENT, NS, f"/apis/apps/v1/namespaces/{NS}/deployments"),
        (Resource.SERVICE, NS, f"/api/v1/namespaces/{NS}/services"),
        (
            Resource.HTTP_ROUTE,
            NS,
            f"/apis/gateway.networking.k8s.io/v1/namespaces/{NS}/httproutes",
        ),
    ],
)
@pytest.mark.asyncio
async def test_the_inventory_kinds_are_listed_with_and_without_a_selector(
    fake: FakeApiServer,
    worker_api: KubeApi,
    resource: Resource,
    namespace: str | None,
    path: str,
) -> None:
    item = {"metadata": {"name": "one", "labels": {"a": "b"}}}
    fake.put_list(path, [item])

    assert await worker_api.list_objects(resource, namespace) == [item]
    assert await worker_api.list_objects(resource, namespace, "a=b") == [item]

    plain, selected = fake.seen("GET")
    assert plain.path == selected.path == path
    assert "labelSelector" not in plain.query
    assert selected.query["labelSelector"] == "a=b"


@pytest.mark.asyncio
async def test_a_list_follows_its_continue_tokens(fake: FakeApiServer, worker_api: KubeApi) -> None:
    path = f"/apis/apps/v1/namespaces/{NS}/deployments"
    items = [{"metadata": {"name": f"d{i}"}} for i in range(1203)]
    fake.put_list(path, items)

    listed = await worker_api.list_objects(Resource.DEPLOYMENT, NS)

    assert [i["metadata"]["name"] for i in listed] == [f"d{i}" for i in range(1203)]
    pages = fake.seen("GET")
    assert len(pages) == 3
    assert {p.query["limit"] for p in pages} == {"500"}
    assert [p.query.get("continue") for p in pages] == [None, "500", "1000"]


@pytest.mark.asyncio
async def test_a_list_that_never_ends_is_refused(fake: FakeApiServer, worker_api: KubeApi) -> None:
    path = f"/apis/apps/v1/namespaces/{NS}/deployments"
    fake.put_list(path, [{"metadata": {"name": f"d{i}"}} for i in range(100 * 500 + 1)])

    with pytest.raises(ClusterUnavailable, match="pages"):
        await worker_api.list_objects(Resource.DEPLOYMENT, NS)
    assert len(fake.seen("GET")) == 100


@pytest.mark.parametrize(
    ("resource", "namespace"),
    [
        (Resource.SECRET, NS),
        (Resource.ROLE_BINDING, NS),
        (Resource.NAMESPACE, NS),
        (Resource.DEPLOYMENT, None),
    ],
    ids=["secret", "rolebinding", "namespaced-namespace", "cluster-deployment"],
)
@pytest.mark.asyncio
async def test_list_refuses_a_kind_it_never_lists_or_the_wrong_scope(
    fake: FakeApiServer, worker_api: KubeApi, resource: Resource, namespace: str | None
) -> None:
    with pytest.raises(ValueError, match="list"):
        await worker_api.list_objects(resource, namespace)
    assert fake.requests == []


# --- answers become typed errors without the response body --------------------------------------


def authorization_denial(fake: FakeApiServer) -> Json:
    return fake.status(
        403,
        "Forbidden",
        {"kind": "deployments", "group": "apps", "name": "rel-x"},
        message=LEAK_MARKER,
    )


def admission_denial(fake: FakeApiServer) -> Json:
    return fake.status(
        403,
        "Forbidden",
        {"kind": "deployments", "causes": [{"reason": "FieldValueForbidden"}]},
        message=LEAK_MARKER,
    )


def terminating_denial(fake: FakeApiServer) -> Json:
    return fake.status(
        403,
        "Forbidden",
        {
            "name": NS,
            "kind": "namespaces",
            "causes": [{"reason": "NamespaceTerminating", "message": LEAK_MARKER}],
        },
        message=LEAK_MARKER,
    )


def plain(fake: FakeApiServer, code: int, reason: str) -> Json:
    return fake.status(code, reason, message=LEAK_MARKER)


@pytest.mark.parametrize(
    ("code", "body", "expected"),
    [
        (409, "conflict", ApplyConflict),
        (403, "authorization", KubernetesForbidden),
        (403, "terminating", KubernetesForbidden),
        (403, "admission", ClusterRefused),
        (400, "bad", ClusterRefused),
        (422, "invalid", ClusterRefused),
        (404, "missing", KubernetesNotFound),
        (429, "busy", ClusterUnavailable),
        (500, "internal", ClusterUnavailable),
        (503, "unavailable", ClusterUnavailable),
        (401, "unauthorized", ClusterUnavailable),
    ],
)
@pytest.mark.asyncio
async def test_apply_answers_map_to_typed_errors_without_the_body(
    fake: FakeApiServer,
    worker_api: KubeApi,
    code: int,
    body: str,
    expected: type[Exception],
) -> None:
    answers = {
        "authorization": authorization_denial(fake),
        "terminating": terminating_denial(fake),
        "admission": admission_denial(fake),
    }
    fake.script(
        "PATCH", DEPLOYMENT_PATH, (code, answers.get(body) or plain(fake, code, "Whatever"))
    )

    with pytest.raises(expected) as caught:
        await worker_api.apply(Resource.DEPLOYMENT, deployment())

    assert LEAK_MARKER not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def schema_refusal() -> Json:
    """Kubernetes 1.36 answers a server-side apply carrying a field its schema does not declare
    with this 500 (measured live on Kind, task D): no reason, no details, only the message."""
    return {
        "kind": "Status",
        "apiVersion": "v1",
        "metadata": {},
        "status": "Failure",
        "message": "failed to create typed patch object (wb-x/rel-x; apps/v1, Kind=Deployment): "
        ".spec.notAField: field not declared in schema",
        "code": 500,
    }


@pytest.mark.asyncio
async def test_an_apply_the_schema_cannot_type_is_refused_not_unavailable(
    fake: FakeApiServer, worker_api: KubeApi
) -> None:
    fake.script("PATCH", DEPLOYMENT_PATH, (500, schema_refusal()))

    with pytest.raises(ClusterRefused) as caught:
        await worker_api.apply(Resource.DEPLOYMENT, deployment())

    assert "notAField" not in str(caught.value)


@pytest.mark.asyncio
async def test_another_500_on_apply_stays_unavailable(
    fake: FakeApiServer, worker_api: KubeApi
) -> None:
    body = schema_refusal()
    body["message"] = "etcdserver: request timed out"
    fake.script("PATCH", DEPLOYMENT_PATH, (500, body))

    with pytest.raises(ClusterUnavailable):
        await worker_api.apply(Resource.DEPLOYMENT, deployment())


@pytest.mark.asyncio
async def test_a_failed_delete_precondition_is_a_conflict_not_an_apply_conflict(
    fake: FakeApiServer, worker_api: KubeApi
) -> None:
    fake.script("DELETE", DEPLOYMENT_PATH, (409, plain(fake, 409, "Conflict")))

    with pytest.raises(KubernetesConflict) as caught:
        await worker_api.delete(Resource.DEPLOYMENT, "rel-x", NS, uid="u", resource_version="1")
    assert not isinstance(caught.value, ApplyConflict)


@pytest.mark.asyncio
async def test_a_refused_connection_is_cluster_unavailable(
    make_kubeconfig: KubeconfigFactory, unused_port: int
) -> None:
    api = await connect(make_kubeconfig(f"https://127.0.0.1:{unused_port}"))
    try:
        with pytest.raises(ClusterUnavailable):
            await api.read(Resource.DEPLOYMENT, "rel-x", NS)
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_a_server_that_never_answers_is_cluster_unavailable_within_the_timeout(
    make_kubeconfig: KubeconfigFactory, silent_server: str
) -> None:
    api = await connect_kubeconfig(
        make_kubeconfig(silent_server),
        field_manager=WORKER_FIELD_MANAGER,
        request_timeout_seconds=0.3,
    )
    try:
        async with asyncio.timeout(5):
            with pytest.raises(ClusterUnavailable):
                await api.read(Resource.DEPLOYMENT, "rel-x", NS)
    finally:
        await api.close()
