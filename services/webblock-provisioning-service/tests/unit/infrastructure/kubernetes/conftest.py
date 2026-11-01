"""A local fake Kubernetes API server for the adapter's unit tests (task D, decision 0014).

No cluster is created or contacted. kubernetes_asyncio fixes its default kubeconfig path from
``$KUBECONFIG`` or ``~/.kube/config`` **when it is imported**, so this file sets ``KUBECONFIG`` to
``DEFAULT_KUBECONFIG_POISON`` (a path that never exists) before anything imports the library: a
regression that fell back to the default would find no file and fail, and could never open the
owner's real kubeconfig. ``test_the_library_default_is_the_poisoned_path`` checks that this held.
In addition every test runs with ``HOME`` and ``KUBECONFIG`` pointing at a poisoned kubeconfig
(``poison.invalid``), and the poisoned-environment proof itself runs in a fresh interpreter whose
environment is poisoned before import (decision 0013 section 9, real-cluster safety). Proxy
variables are removed because aiohttp honours them.

The fake server speaks HTTPS on 127.0.0.1 with a throwaway CA made here, records every request,
and answers like the API server does for the calls the adapter makes: GET of a stored object or a
404 ``Status``, server-side apply (PATCH) that stores and echoes the object, DELETE that removes it,
and label-selected lists. Tests can script answers per method and path, which are used first.
"""

import asyncio
import base64
import datetime
import ipaddress
import json
import os
import socket
import ssl
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

# Before the first import of kubernetes_asyncio (through the package below): see the docstring.
DEFAULT_KUBECONFIG_POISON = str(
    Path(__file__).resolve().parent / "never-a-kubeconfig" / "poisoned-default"
)
os.environ["KUBECONFIG"] = DEFAULT_KUBECONFIG_POISON

from provisioning.infrastructure.kubernetes import (  # noqa: E402 - after the poisoning above
    WORKER_FIELD_MANAGER,
    KubeApi,
    connect_kubeconfig,
)

type Json = dict[str, Any]
type KubeconfigFactory = Callable[..., Path]

FAKE_TOKEN = "fake-worker-token-for-unit-tests"  # noqa: S105 - a test value, never a credential
REQUEST_TIMEOUT = 5.0
POISON_SERVER = "https://poison.invalid:6443"

LIST_KINDS = {
    "pods": "PodList",
    "endpointslices": "EndpointSliceList",
    "namespaces": "NamespaceList",
    "deployments": "DeploymentList",
    "services": "ServiceList",
    "httproutes": "HTTPRouteList",
}
LIST_API_VERSIONS = {
    "pods": "v1",
    "endpointslices": "discovery.k8s.io/v1",
    "namespaces": "v1",
    "deployments": "apps/v1",
    "services": "v1",
    "httproutes": "gateway.networking.k8s.io/v1",
}


def status_body(code: int, reason: str, details: Json | None = None, message: str = "") -> Json:
    """A ``metav1.Status`` failure body as the API server sends it."""
    body: Json = {
        "kind": "Status",
        "apiVersion": "v1",
        "metadata": {},
        "status": "Failure",
        "message": message or reason,
        "reason": reason,
        "code": code,
    }
    if details is not None:
        body["details"] = details
    return body


@dataclass(frozen=True, slots=True)
class Seen:
    """One request the fake server received."""

    method: str
    path: str
    query: dict[str, str]
    content_type: str | None
    authorization: str | None
    body: Any


@dataclass(slots=True)
class FakeApiServer:
    url: str = ""
    requests: list[Seen] = field(default_factory=list)
    objects: dict[str, Json] = field(default_factory=dict)
    lists: dict[str, list[Json]] = field(default_factory=dict)
    scripted: dict[tuple[str, str], list[tuple[int, Json]]] = field(default_factory=dict)
    _version: int = 100

    @staticmethod
    def status(code: int, reason: str, details: Json | None = None, message: str = "") -> Json:
        return status_body(code, reason, details, message)

    def script(self, method: str, path: str, *answers: tuple[int, Json]) -> None:
        """Answer the next requests to ``method path`` with these, in order, before the default."""
        self.scripted.setdefault((method, path), []).extend(answers)

    def put(self, path: str, obj: Json) -> None:
        self.objects[path] = obj

    def put_list(self, path: str, items: list[Json]) -> None:
        self.lists[path] = items

    def seen(self, method: str | None = None) -> list[Seen]:
        return [r for r in self.requests if method is None or r.method == method]

    def mutations(self) -> list[Seen]:
        return [r for r in self.requests if r.method in ("PATCH", "DELETE", "POST", "PUT")]

    def _next_version(self) -> str:
        self._version += 1
        return str(self._version)

    async def handle(self, request: web.Request) -> web.Response:
        raw = await request.read()
        body: Any = json.loads(raw) if raw else None
        path = request.path
        self.requests.append(
            Seen(
                method=request.method,
                path=path,
                query=dict(request.query),
                content_type=request.headers.get("Content-Type"),
                authorization=request.headers.get("Authorization"),
                body=body,
            )
        )
        queue = self.scripted.get((request.method, path))
        if queue:
            code, answer = queue.pop(0)
            return web.json_response(answer, status=code)
        if request.method == "GET":
            return self._get(path, dict(request.query))
        if request.method == "PATCH":
            return self._apply(path, body)
        if request.method == "DELETE":
            return self._delete(path)
        return web.json_response(status_body(405, "MethodNotAllowed"), status=405)

    def _get(self, path: str, query: dict[str, str]) -> web.Response:
        if path in self.objects:
            return web.json_response(self.objects[path])
        last = path.rsplit("/", 1)[-1]
        if last in LIST_KINDS:
            # Pages as the API server serves them: ``limit`` items, then a ``continue`` token
            # (here the index of the next item) while more remain.
            items = self.lists.get(path, [])
            start = int(query.get("continue") or 0)
            limit = int(query.get("limit") or 0) or len(items) or 1
            metadata: Json = {"resourceVersion": "1"}
            if start + limit < len(items):
                metadata["continue"] = str(start + limit)
            return web.json_response(
                {
                    "kind": LIST_KINDS[last],
                    "apiVersion": LIST_API_VERSIONS[last],
                    "metadata": metadata,
                    "items": items[start : start + limit],
                }
            )
        return web.json_response(status_body(404, "NotFound"), status=404)

    def _apply(self, path: str, body: Json) -> web.Response:
        old = self.objects.get(path, {}).get("metadata", {})
        meta = dict(body.get("metadata", {}))
        meta["uid"] = old.get("uid", f"uid-{len(self.objects) + 1}")
        meta["resourceVersion"] = self._next_version()
        meta["generation"] = int(old.get("generation", 0)) + 1
        meta["creationTimestamp"] = "2026-10-08T00:00:00Z"
        stored = dict(body)
        stored["metadata"] = meta
        if body.get("kind") == "Namespace":
            stored["status"] = {"phase": "Active"}
        self.objects[path] = stored
        return web.json_response(stored)

    def _delete(self, path: str) -> web.Response:
        if path not in self.objects:
            return web.json_response(status_body(404, "NotFound"), status=404)
        del self.objects[path]
        return web.json_response(
            {"kind": "Status", "apiVersion": "v1", "metadata": {}, "status": "Success"}
        )


@dataclass(frozen=True, slots=True)
class Tls:
    ca_pem: str
    cert_file: Path
    key_file: Path


@pytest.fixture(scope="session")
def tls(tmp_path_factory: pytest.TempPathFactory) -> Tls:
    """A throwaway CA and a server certificate for 127.0.0.1."""
    folder = tmp_path_factory.mktemp("tls")
    now = datetime.datetime.now(datetime.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake-kube-ca")])
    ca_ski = x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key())
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(ca_ski, critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski), critical=False
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    cert_file = folder / "server.pem"
    key_file = folder / "server.key"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return Tls(
        ca_pem=ca.public_bytes(serialization.Encoding.PEM).decode(),
        cert_file=cert_file,
        key_file=key_file,
    )


def kubeconfig_text(
    *,
    server: str,
    ca_pem: str,
    context: str = "kind-webblock-unit",
    user: Json | None = None,
    cluster_extra: Json | None = None,
) -> str:
    """A kubeconfig as JSON, which is valid YAML."""
    cluster: Json = {
        "server": server,
        "certificate-authority-data": base64.b64encode(ca_pem.encode()).decode(),
    }
    cluster.update(cluster_extra or {})
    return json.dumps(
        {
            "apiVersion": "v1",
            "kind": "Config",
            "current-context": context,
            "clusters": [{"name": "c", "cluster": cluster}],
            "contexts": [{"name": context, "context": {"cluster": "c", "user": "u"}}],
            "users": [{"name": "u", "user": user if user is not None else {"token": FAKE_TOKEN}}],
        },
        indent=2,
    )


@pytest.fixture(autouse=True)
def poisoned_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tls: Tls) -> Path:
    """HOME and KUBECONFIG point at a kubeconfig that must never be used; proxies are removed."""
    home = tmp_path / "poisoned-home"
    (home / ".kube").mkdir(parents=True)
    poisoned = home / ".kube" / "config"
    poisoned.write_text(kubeconfig_text(server=POISON_SERVER, ca_pem=tls.ca_pem))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("KUBECONFIG", str(poisoned))
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "KUBERNETES_SERVICE_HOST",
        "KUBERNETES_SERVICE_PORT",
    ):
        monkeypatch.delenv(name, raising=False)
    return poisoned


@pytest_asyncio.fixture
async def fake(tls: Tls) -> AsyncIterator[FakeApiServer]:
    server = FakeApiServer()
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", server.handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(tls.cert_file, tls.key_file)
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=context)
    await site.start()
    port = runner.addresses[0][1]
    server.url = f"https://127.0.0.1:{port}"
    try:
        yield server
    finally:
        await runner.cleanup()


@pytest.fixture
def make_kubeconfig(tmp_path: Path, tls: Tls) -> KubeconfigFactory:
    """Write a project-local kubeconfig into the test's folder and return its absolute path."""
    counter = iter(range(1, 1000))

    def make(server: str, **kwargs: Any) -> Path:
        path = tmp_path / f"kubeconfig-{next(counter)}"
        path.write_text(kubeconfig_text(server=server, ca_pem=tls.ca_pem, **kwargs))
        return path

    return make


@pytest_asyncio.fixture
async def worker_api(
    fake: FakeApiServer, make_kubeconfig: KubeconfigFactory
) -> AsyncIterator[KubeApi]:
    api = await connect_kubeconfig(
        make_kubeconfig(fake.url),
        field_manager=WORKER_FIELD_MANAGER,
        request_timeout_seconds=REQUEST_TIMEOUT,
    )
    try:
        yield api
    finally:
        await api.close()


class PoisonListener:
    """A TCP listener on 127.0.0.1 named by the poisoned kubeconfigs; it counts connections."""

    def __init__(self) -> None:
        self.files: tuple[Path, ...] = ()
        self.connections = 0

    def accepted(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        writer.close()


@pytest_asyncio.fixture
async def tls_poison(
    poisoned_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[PoisonListener]:
    """Point the poisoned HOME and KUBECONFIG files at a counting listener on 127.0.0.1, with a
    context the wrapper would accept, so that only the explicit path protects the test."""
    listener = PoisonListener()
    server = await asyncio.start_server(listener.accepted, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    text = kubeconfig_text(
        server=f"https://127.0.0.1:{port}", ca_pem="poison", context="kind-webblock-poison"
    )
    # Two small files written before any request; nothing else runs on the loop yet.
    poisoned_home.write_text(text)  # noqa: ASYNC240 - test setup, see above
    second = tmp_path / "kubeconfig-from-env"
    second.write_text(text)
    monkeypatch.setenv("KUBECONFIG", str(second))
    listener.files = (poisoned_home, second)
    try:
        yield listener
    finally:
        server.close()
        await server.wait_closed()


@pytest.fixture
def unused_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
    return port


@pytest_asyncio.fixture
async def silent_server() -> AsyncIterator[str]:
    """A TCP server on 127.0.0.1 that accepts connections and never answers."""
    held: list[asyncio.StreamWriter] = []

    def keep(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        held.append(writer)

    server = await asyncio.start_server(keep, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"https://127.0.0.1:{port}"
    finally:
        for writer in held:
            writer.close()
        server.close()
        await server.wait_closed()
