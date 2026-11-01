"""A local token issuer for the JWT verifier's tests (decision 0024): keys generated here, a fake
issuer behind ``httpx2.MockTransport`` that serves the discovery document and the key set the way
Keycloak does, counts its hits and can answer with an error status (the verifier takes the
transport through web-block-commons' test-only ``transport`` argument), and, for the trust tests
(TR-26), a test certificate authority made with ``cryptography`` and the same issuer over real
HTTPS on 127.0.0.1. Nothing here is imported by the service; not a test module."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime as dt
import ipaddress
import json
import ssl
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import httpx2
import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

ISSUER = "https://issuer.test/realms/webblock"
AUDIENCE = "provisioning"
DISCOVERY_PATH = "/realms/webblock/.well-known/openid-configuration"
JWKS_PATH = "/realms/webblock/protocol/openid-connect/certs"
USER_ID = "0199e7a0-0000-7000-8000-0000000000aa"
T0 = 1_791_633_600.0  # 2026-10-10 12:00:00 UTC

type PrivateKey = rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey


@dataclass(frozen=True)
class KeyPair:
    """One signing key, its key ID and the algorithm a test signs with."""

    kid: str
    alg: str
    private: PrivateKey

    def jwk(self, **extra: Any) -> dict[str, Any]:
        """The public key as a JWK with ``kid`` and ``use: sig``, plus ``extra`` members."""
        public = self.private.public_key()
        if isinstance(public, rsa.RSAPublicKey):
            doc = RSAAlgorithm.to_jwk(public, as_dict=True)
        else:
            doc = ECAlgorithm.to_jwk(public, as_dict=True)
        return {**doc, "kid": self.kid, "use": "sig", **extra}


@cache
def _rsa(bits: int, seed: str) -> rsa.RSAPrivateKey:
    del seed  # one key per (bits, seed): the cache makes each key once per test session
    return rsa.generate_private_key(public_exponent=65537, key_size=bits)


@cache
def _ec() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def rsa_key(kid: str = "rsa-1", *, seed: str = "a", bits: int = 2048) -> KeyPair:
    return KeyPair(kid=kid, alg="RS256", private=_rsa(bits, seed))


def ec_key(kid: str = "ec-1") -> KeyPair:
    return KeyPair(kid=kid, alg="ES256", private=_ec())


def claims(now: float = T0, **changes: Any) -> dict[str, Any]:
    """Valid claims at ``now``, as Keycloak issues an access token: issuer, audience, ``typ``
    Bearer, subject, user ID, the ``USER`` role and five minutes of life. A change to None removes
    the claim."""
    base: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "typ": "Bearer",
        "sub": "agent:alice",
        "user_id": USER_ID,
        "roles": ["USER"],
        "iat": int(now),
        "exp": int(now) + 300,
    }
    for name, value in changes.items():
        if value is None:
            base.pop(name, None)
        else:
            base[name] = value
    return base


def mint(
    key: KeyPair,
    payload: Mapping[str, Any] | None = None,
    *,
    alg: str | None = None,
    headers: Mapping[str, Any] | None = None,
) -> str:
    """A compact JWS signed by ``key``, with the key's ``kid``."""
    head: dict[str, Any] = {**(headers or {}), "kid": key.kid}
    # PyJWS signs the payload bytes as they are: no claim is converted or checked on the way.
    body = json.dumps(dict(payload if payload is not None else claims())).encode()
    return jwt.PyJWS().encode(body, key.private, algorithm=alg or key.alg, headers=head)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unsigned(header: Mapping[str, Any], payload: Mapping[str, Any], signature: str = "") -> str:
    """A token assembled by hand (for ``alg: none`` and shapes no library would sign)."""
    return ".".join(
        (b64(json.dumps(header).encode()), b64(json.dumps(payload).encode()), signature)
    )


@dataclass
class FakeIssuer:
    """The issuer's discovery document and key set. ``status`` other than 200 makes every answer
    that status; ``discovery_hits`` and ``jwks_hits`` count the requests of each path, ``paths``
    every path asked for."""

    keys: list[dict[str, Any]]
    status: int = 200
    discovery_hits: int = 0
    jwks_hits: int = 0
    paths: list[str] = field(default_factory=list)

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._answer)

    def _answer(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        self.paths.append(path)
        if path == DISCOVERY_PATH:
            self.discovery_hits += 1
        elif path == JWKS_PATH:
            self.jwks_hits += 1
        if self.status != 200:
            return httpx2.Response(self.status, text="down")
        if request.url.host != "issuer.test":
            return httpx2.Response(404)
        if path == DISCOVERY_PATH:
            return httpx2.Response(
                200, json={"issuer": ISSUER, "jwks_uri": f"https://issuer.test{JWKS_PATH}"}
            )
        if path == JWKS_PATH:
            return httpx2.Response(200, json={"keys": self.keys})
        return httpx2.Response(404)


# --- the test certificate authority -------------------------------------------------------------


@dataclass(frozen=True)
class LocalCA:
    ca_pem: bytes
    server_cert_pem: bytes
    server_key_pem: bytes

    def client_context(self) -> ssl.SSLContext:
        """What a test passes to the verifier's constructor: trust in this CA only."""
        ctx = ssl.create_default_context(cadata=self.ca_pem.decode())
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx

    def server_context(self, tmp: Path) -> ssl.SSLContext:
        cert = tmp / "server.crt"
        key = tmp / "server.key"
        cert.write_bytes(self.server_cert_pem)
        key.write_bytes(self.server_key_pem)
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(cert, key)
        return ctx


@cache
def local_ca() -> LocalCA:
    now = dt.datetime.now(dt.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "webblock test CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(hours=1))
        .not_valid_after(now + dt.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
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
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    server_key = ec.generate_private_key(ec.SECP256R1())
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(hours=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return LocalCA(
        ca_pem=ca_cert.public_bytes(serialization.Encoding.PEM),
        server_cert_pem=server_cert.public_bytes(serialization.Encoding.PEM),
        server_key_pem=server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


# ``openssl x509 -hash`` of the test CA's fixed subject, CN=webblock test CA: the file name OpenSSL
# looks up in a CA directory. The trust test's neighbour fails if it is wrong.
TEST_CA_HASH_NAME = "1282c418.0"


def trusts_test_ca(ctx: ssl.SSLContext) -> bool:
    """Whether the context holds the test CA among its loaded roots."""
    return ssl.PEM_cert_to_DER_cert(local_ca().ca_pem.decode()) in ctx.get_ca_certs(True)


# --- the issuer over HTTPS --------------------------------------------------------------------


@dataclass
class HttpsIssuer:
    """The issuer's discovery document and key set over HTTPS on 127.0.0.1, with the test CA's
    certificate. ``hits`` counts the requests that got past the TLS handshake."""

    keys: list[dict[str, Any]]
    port: int = 0
    hits: int = 0
    _server: asyncio.Server | None = field(default=None, repr=False)

    @property
    def issuer(self) -> str:
        return f"https://127.0.0.1:{self.port}/realms/webblock"

    def _body(self, path: str) -> tuple[int, bytes]:
        if path == DISCOVERY_PATH:
            doc = {"issuer": self.issuer, "jwks_uri": f"https://127.0.0.1:{self.port}{JWKS_PATH}"}
            return 200, json.dumps(doc).encode()
        if path == JWKS_PATH:
            return 200, json.dumps({"keys": self.keys}).encode()
        return 404, b"{}"

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
            self.hits += 1
            status, body = self._body(head.split(b" ", 2)[1].decode())
            writer.write(
                f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        except ConnectionError, asyncio.IncompleteReadError, TimeoutError, ssl.SSLError:
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, ssl.SSLError, OSError):
                await writer.wait_closed()


@contextlib.asynccontextmanager
async def https_issuer(tmp: Path, *keys: dict[str, Any]) -> AsyncIterator[HttpsIssuer]:
    server = HttpsIssuer(keys=list(keys))
    tls = await asyncio.start_server(
        server._handle, "127.0.0.1", 0, ssl=local_ca().server_context(tmp)
    )
    server.port = tls.sockets[0].getsockname()[1]
    try:
        yield server
    finally:
        tls.close()
        tls.close_clients()
        await tls.wait_closed()
