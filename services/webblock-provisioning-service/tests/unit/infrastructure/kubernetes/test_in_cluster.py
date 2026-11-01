"""connect_in_cluster (decision 0016 section 9 ruling 2): from a pod, with the explicitly projected
token and CA named by absolute path and the API server from KUBERNETES_SERVICE_HOST and
KUBERNETES_SERVICE_PORT, the client is returned only after one SelfSubjectReview names the
expected ServiceAccount. Runs against the fake API server of conftest.py; no cluster is
contacted, and the autouse fixture there removes the two variables and poisons HOME and
KUBECONFIG."""

from __future__ import annotations

import socket
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from provisioning.core.application.ports.site_cluster import ClusterUnavailable
from provisioning.infrastructure.kubernetes import (
    WORKER_FIELD_MANAGER,
    KubeApi,
    KubeconfigRefused,
    Resource,
    connect_in_cluster,
)

if TYPE_CHECKING:
    from tests.unit.infrastructure.kubernetes.conftest import FakeApiServer, Json, Tls

TIMEOUT = 5.0
REVIEW_PATH = "/apis/authentication.k8s.io/v1/selfsubjectreviews"
WORKER = "system:serviceaccount:webblock:provisioning-worker"
BOOTSTRAP = "system:serviceaccount:webblock:tenant-bootstrap"
TOKEN = "projected-token-0001"  # noqa: S105 - a test value, never a credential


def review(username: str | None) -> Json:
    user = {} if username is None else {"username": username, "groups": ["system:serviceaccounts"]}
    return {
        "apiVersion": "authentication.k8s.io/v1",
        "kind": "SelfSubjectReview",
        "metadata": {},
        "status": {"userInfo": user},
    }


@pytest.fixture
def pod_files(tmp_path: Path, tls: Tls) -> tuple[Path, Path]:
    folder = tmp_path / "kube"
    folder.mkdir()
    (folder / "token").write_text(TOKEN)
    (folder / "ca.crt").write_text(tls.ca_pem)
    return folder / "token", folder / "ca.crt"


@pytest.fixture
def in_pod(fake: FakeApiServer, monkeypatch: pytest.MonkeyPatch) -> FakeApiServer:
    host, port = fake.url.removeprefix("https://").split(":")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", host)
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", port)
    return fake


async def connect(files: tuple[Path, Path], expected: str = WORKER) -> KubeApi:
    token, ca = files
    return await connect_in_cluster(
        token_file=token,
        ca_file=ca,
        expected_username=expected,
        field_manager=WORKER_FIELD_MANAGER,
        request_timeout_seconds=TIMEOUT,
    )


@pytest.mark.asyncio
async def test_the_expected_service_account_connects_with_the_projected_token(
    in_pod: FakeApiServer, pod_files: tuple[Path, Path]
) -> None:
    in_pod.script("POST", REVIEW_PATH, (201, review(WORKER)))
    api = await connect(pod_files)
    try:
        assert await api.read(Resource.NAMESPACE, "wb-x") is None
    finally:
        await api.close()
    (asked,) = in_pod.seen("POST")
    assert asked.path == REVIEW_PATH
    assert asked.body == {"apiVersion": "authentication.k8s.io/v1", "kind": "SelfSubjectReview"}
    assert {r.authorization for r in in_pod.requests} == {f"Bearer {TOKEN}"}


@pytest.mark.asyncio
async def test_another_service_account_is_refused(
    in_pod: FakeApiServer, pod_files: tuple[Path, Path]
) -> None:
    """Planted defect: the worker's pod mounted the bootstrap's token."""
    in_pod.script("POST", REVIEW_PATH, (201, review(BOOTSTRAP)))
    with pytest.raises(KubeconfigRefused, match="not the expected ServiceAccount"):
        await connect(pod_files)
    assert [r.method for r in in_pod.requests] == ["POST"]


@pytest.mark.asyncio
async def test_a_review_without_a_username_is_refused(
    in_pod: FakeApiServer, pod_files: tuple[Path, Path]
) -> None:
    in_pod.script("POST", REVIEW_PATH, (201, review(None)))
    with pytest.raises(KubeconfigRefused, match="not the expected ServiceAccount"):
        await connect(pod_files)


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.asyncio
async def test_a_refused_token_is_refused(
    in_pod: FakeApiServer, pod_files: tuple[Path, Path], status: int
) -> None:
    in_pod.script("POST", REVIEW_PATH, (status, in_pod.status(status, "Unauthorized")))
    with pytest.raises(KubeconfigRefused, match=f"HTTP {status}"):
        await connect(pod_files)


@pytest.mark.asyncio
async def test_a_rotated_token_is_read_again(
    in_pod: FakeApiServer, pod_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The kubelet replaces the projected token before it expires (600 s); the library reads the
    file again once its last read is older than its refresh period (one minute; zero here)."""
    monkeypatch.setattr(
        "kubernetes_asyncio.config.incluster_config.TOKEN_REFRESH_PERIOD", timedelta(0)
    )
    in_pod.script("POST", REVIEW_PATH, (201, review(WORKER)))
    api = await connect(pod_files)
    try:
        pod_files[0].write_text("projected-token-0002")
        await api.read(Resource.NAMESPACE, "wb-x")
    finally:
        await api.close()
    assert in_pod.requests[-1].authorization == "Bearer projected-token-0002"


@pytest.mark.asyncio
async def test_an_api_server_that_does_not_answer_is_unavailable(
    pod_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "127.0.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", str(port))
    with pytest.raises(ClusterUnavailable):
        await connect(pod_files)


@pytest.mark.asyncio
async def test_outside_a_cluster_is_refused(pod_files: tuple[Path, Path]) -> None:
    with pytest.raises(KubeconfigRefused, match="not running in a cluster"):
        await connect(pod_files)


@pytest.mark.parametrize("which", ["token", "ca"])
@pytest.mark.parametrize("problem", ["missing", "relative"])
@pytest.mark.asyncio
async def test_the_pod_files_must_exist_by_absolute_path(
    in_pod: FakeApiServer, pod_files: tuple[Path, Path], which: str, problem: str
) -> None:
    token, ca = pod_files
    bad = token.parent / "absent" if problem == "missing" else Path("kube") / "token"
    files = (bad, ca) if which == "token" else (token, bad)
    with pytest.raises(KubeconfigRefused, match="existing files named by absolute path"):
        await connect(files)
    assert in_pod.requests == []


@pytest.mark.asyncio
async def test_an_unreadable_token_file_is_refused_without_its_path(
    in_pod: FakeApiServer, pod_files: tuple[Path, Path]
) -> None:
    """A projected token owned by another user, or mode 000: a clear refusal, not a raw OSError."""
    token, _ = pod_files
    token.chmod(0)
    try:
        with pytest.raises(
            KubeconfigRefused, match="the token or CA file cannot be read"
        ) as caught:
            await connect(pod_files)
    finally:
        token.chmod(0o600)
    assert str(token) not in str(caught.value)
    assert in_pod.requests == []


@pytest.mark.asyncio
async def test_a_ca_file_that_is_not_pem_is_refused_without_its_content(
    in_pod: FakeApiServer, pod_files: tuple[Path, Path]
) -> None:
    """A mis-keyed CA ConfigMap: a clear refusal, not an SSLError."""
    _, ca = pod_files
    ca.write_text("CA-CONTENT-MARKER-not-a-certificate")
    with pytest.raises(KubeconfigRefused, match="the token or CA file cannot be read") as caught:
        await connect(pod_files)
    assert "CA-CONTENT-MARKER" not in str(caught.value)
    assert str(ca) not in str(caught.value)
    assert in_pod.requests == []
