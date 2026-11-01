"""The JWT verifier on top of web-block-commons (decision 0024 G4 to G8).

The library's own tests cover each of its token and key set rules. These tests cover how
Provisioning uses it: a sample of the library's refusals, the checks Provisioning adds (G5), the
user and the roles, the 503 of a key set that cannot be had, the trust roots of the key set fetch
(G4a), the settings it refuses, and the log lines and metric causes of the bridge (G8), with no
token, claim value or key in the log. Each test names its own requirement."""

from __future__ import annotations

import io
import json
import logging
import ssl
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx2
import pytest
from tests.unit.infrastructure.identity.issuer import (
    AUDIENCE,
    ISSUER,
    T0,
    TEST_CA_HASH_NAME,
    USER_ID,
    FakeIssuer,
    claims,
    ec_key,
    https_issuer,
    local_ca,
    mint,
    rsa_key,
    unsigned,
)

from provisioning.core.application import ActionDenied, Principal, Unauthenticated
from provisioning.infrastructure.config.identity import JWTConfig
from provisioning.infrastructure.identity import (
    IdentityKeysUnavailable,
    JwtVerifier,
    attach_library_logs,
)
from provisioning.infrastructure.telemetry import new_logger
from provisioning.infrastructure.telemetry.metrics import AUTH_CAUSES, KEY_FETCH_CAUSES

pytestmark = [pytest.mark.unit]

RSA = rsa_key()
FORGER = rsa_key("rsa-1", seed="forger")  # the same kid as RSA, a different key
LIFETIME = 900


@dataclass
class Clock:
    now: float = T0

    def __call__(self) -> float:
        return self.now


@dataclass
class Harness:
    issuer: FakeIssuer
    clock: Clock = field(default_factory=Clock)
    log: io.StringIO = field(default_factory=io.StringIO)
    verifier: JwtVerifier = field(init=False)

    def __post_init__(self) -> None:
        self.verifier = JwtVerifier(
            JWTConfig(
                issuer=ISSUER, audience=AUDIENCE, max_token_lifetime=timedelta(seconds=LIFETIME)
            ),
            logger=new_logger(self.log, logging.DEBUG),
            clock=self.clock,
            transport=self.issuer.transport(),
        )

    async def verify(self, token: str) -> Principal:
        return await self.verifier.verify(token)

    async def refused(self, token: str) -> None:
        with pytest.raises(Unauthenticated) as err:
            await self.verifier.verify(token)
        assert str(err.value) == "token refused"
        assert err.value.__cause__ is None

    def records(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.log.getvalue().splitlines() if line]

    def causes(self) -> list[str]:
        return [r["auth_cause"] for r in self.records() if "auth_cause" in r]


@pytest.fixture
def h() -> Iterator[Harness]:
    harness = Harness(FakeIssuer(keys=[RSA.jwk(alg="RS256")]))
    detach = attach_library_logs(new_logger(harness.log, logging.DEBUG))
    try:
        yield harness
    finally:
        detach()


def config(**changes: Any) -> JWTConfig:
    base: dict[str, Any] = {
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "max_token_lifetime": timedelta(seconds=LIFETIME),
    }
    return JWTConfig(**{**base, **changes})


# --- accepted tokens (FR-1.1, FR-2.1, TR-27) ------------------------------------------------------


@pytest.mark.asyncio
async def test_fr2_1_a_valid_token_names_its_subject_roles_and_user(h: Harness) -> None:
    """FR-2.1: the principal is the subject, the top-level roles and the user ID."""
    principal = await h.verify(mint(RSA, claims(roles=["USER", "ADMIN"])))
    assert principal == Principal(
        subject="agent:alice", scopes=("USER", "ADMIN"), user_id=UUID(USER_ID)
    )
    assert (h.issuer.discovery_hits, h.issuer.jwks_hits) == (1, 1)


@pytest.mark.asyncio
async def test_fr1_1_an_audience_list_holding_provisioning_is_accepted(h: Harness) -> None:
    """FR-1.1: ``aud`` may be a list holding ``provisioning``."""
    principal = await h.verify(mint(RSA, claims(aud=["account", AUDIENCE])))
    assert principal.user_id == UUID(USER_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("roles", "scopes"),
    [
        (None, ()),
        ("ADMIN", ()),  # not a list: no roles
        (["ADMIN", 1], ()),  # not a list of text: no roles
        (["admin", "Admin"], ("admin", "Admin")),  # kept whole; neither is ADMIN
    ],
)
async def test_tr27_roles_come_only_from_a_top_level_list(
    h: Harness, roles: object, scopes: tuple[str, ...]
) -> None:
    """TR-27: roles only from the top-level ``roles`` list, compared whole and with case;
    ``realm_access`` is never read."""
    token = mint(RSA, claims(roles=roles, realm_access={"roles": ["ADMIN"]}))
    principal = await h.verify(token)
    assert principal.scopes == scopes
    assert not principal.has_scope("ADMIN")


# --- FR-2.2: no user ------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_id",
    [
        None,
        "",
        "not-a-uuid",
        USER_ID.upper(),
        USER_ID.replace("-", ""),
        "{" + USER_ID + "}",
        "00000000-0000-0000-0000-000000000000",
        42,
        True,
        [USER_ID],
        {"id": USER_ID},
    ],
)
async def test_fr2_2_a_token_without_a_valid_user_id_is_access_denied(
    h: Harness, user_id: object
) -> None:
    """FR-2.2: a valid token with no valid ``user_id`` is ``ActionDenied`` (403), logged with
    the fixed cause ``user_id_missing``; the claim's value is never logged."""
    with pytest.raises(ActionDenied):
        await h.verify(mint(RSA, claims(user_id=user_id)))
    assert h.causes() == ["user_id_missing"]
    assert "not-a-uuid" not in h.log.getvalue()


@pytest.mark.asyncio
async def test_fr2_2_a_refused_token_is_refused_before_its_user_is_looked_at(h: Harness) -> None:
    """FR-2.2: a refused token stays 401, never 403, whatever its user."""
    await h.refused(mint(RSA, claims(user_id=None, iss=ISSUER + "x")))
    await h.refused(mint(RSA, claims(user_id=None, sub=None)))
    assert h.causes() == ["issuer_mismatch", "sub_missing"]


# --- the library's refusals (FR-1.2, TR-26) -------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "cause"),
    [
        ({"typ": "ID"}, "typ_mismatch"),
        ({"typ": "bearer"}, "typ_mismatch"),
        ({"typ": None}, "typ_mismatch"),
        ({"typ": 1}, "typ_mismatch"),
        ({"iss": ISSUER + "/"}, "issuer_mismatch"),
        ({"iss": ISSUER.upper()}, "issuer_mismatch"),
        ({"aud": "provisioning-x"}, "audience_mismatch"),
        ({"aud": ["account"]}, "audience_mismatch"),
        ({"aud": None}, "audience_mismatch"),
        ({"exp": None}, "exp_missing"),
        ({"exp": int(T0) - 31, "iat": int(T0) - 300}, "expired"),
        ({"nbf": int(T0) + 31}, "not_yet_valid"),
    ],
)
async def test_fr1_2_the_library_refuses_each_bad_claim(
    h: Harness, changes: dict[str, Any], cause: str
) -> None:
    """FR-1.2: ``typ`` other than ``Bearer``, another issuer, no ``provisioning`` audience, no or a
    passed ``exp``, a future ``nbf``: 401 with the library's fixed cause."""
    await h.refused(mint(RSA, claims(**changes)))
    assert h.causes() == [cause]


@pytest.mark.asyncio
async def test_fr1_2_the_skew_is_30_seconds(h: Harness) -> None:
    """TR-26: ``exp`` and ``nbf`` are honoured within the library's 30 s skew (at most 60 s)."""
    await h.verify(mint(RSA, claims(exp=int(T0) - 30, iat=int(T0) - 300)))
    await h.verify(mint(RSA, claims(nbf=int(T0) + 30)))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token", "cause"),
    [
        (lambda: mint(ec_key(), claims()), "alg_not_allowed"),
        (lambda: unsigned({"alg": "none", "kid": "rsa-1"}, claims(), "c2ln"), "alg_not_allowed"),
        (lambda: unsigned({"alg": "HS256", "kid": "rsa-1"}, claims(), "c2ln"), "alg_not_allowed"),
        (lambda: mint(FORGER, claims()), "bad_signature"),
        (lambda: mint(RSA, claims(), headers={"jku": "https://x.test/k"}), "forbidden_header"),
        (lambda: mint(rsa_key("unknown-kid", seed="b"), claims()), "unknown_kid"),
        (lambda: "a.b", "token_format"),
        (lambda: "x" * 9000, "token_size"),
    ],
)
async def test_fr1_2_rs256_only_and_a_signature_of_a_published_key(
    h: Harness, token: Any, cause: str
) -> None:
    """FR-1.2, TR-26: RS256 only (``none``, HMAC and ES256 refused), the issuer's key, no header
    that names a key, the token's shape."""
    await h.refused(token())
    assert h.causes() == [cause]


# --- the claims Provisioning adds (decision 0024 G5) ----------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "cause"),
    [
        ({"iat": int(T0) + 31, "exp": int(T0) + 300}, "iat_in_future"),
        ({"iat": int(T0) - 10, "exp": int(T0) - 10 + LIFETIME + 1}, "lifetime_exceeded"),
        ({"iat": None, "exp": int(T0) + LIFETIME + 31}, "lifetime_exceeded"),
        ({"iat": int(T0) + 20, "exp": int(T0) + 20}, "claims_malformed"),
        ({"nbf": int(T0) + 20, "exp": int(T0) + 20}, "claims_malformed"),
        ({"iat": "now"}, "claims_malformed"),
        ({"sub": None}, "sub_missing"),
        ({"sub": 7}, "sub_invalid"),
        ({"sub": ""}, "sub_invalid"),
        ({"sub": "a\nb"}, "sub_invalid"),
    ],
)
async def test_g5_provisioning_refuses_bad_times_and_subjects(
    h: Harness, changes: dict[str, Any], cause: str
) -> None:
    """TR-26, decision 0024 G5: a future ``iat``, a lifetime over the cap (from ``iat`` and now),
    ``exp`` not after ``iat`` or ``nbf``, and a missing or invalid ``sub`` are 401."""
    await h.refused(mint(RSA, claims(**changes)))
    assert h.causes() == [cause]


@pytest.mark.asyncio
async def test_g5_a_lifetime_at_the_cap_is_accepted(h: Harness) -> None:
    """TR-26, decision 0024 G5: a lifetime at the cap, from ``iat`` or from now, is accepted."""
    await h.verify(mint(RSA, claims(iat=int(T0), exp=int(T0) + LIFETIME)))
    await h.verify(mint(RSA, claims(iat=None, exp=int(T0) + LIFETIME + 30)))


# --- the key set (FR-1.3) -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fr1_3_a_key_set_that_cannot_be_had_is_unavailable_not_unauthenticated(
    h: Harness,
) -> None:
    """FR-1.3: no key set is ``IdentityKeysUnavailable`` (503), never a refusal (401)."""
    h.issuer.status = 503
    with pytest.raises(IdentityKeysUnavailable) as err:
        await h.verify(mint(RSA))
    assert not isinstance(err.value, Unauthenticated)
    assert h.causes() == ["fetch_status", "keys_unavailable"]
    assert [r["msg"] for r in h.records()] == ["jwks fetch failed", "identity keys unavailable"]


@pytest.mark.asyncio
async def test_fr1_3_a_transport_failure_logs_its_cause_without_the_class_name(h: Harness) -> None:
    """FR-1.3: a transport failure is 503, logged with its cause and no exception class."""

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    verifier = JwtVerifier(
        config(), logger=new_logger(h.log, logging.DEBUG), transport=httpx2.MockTransport(refuse)
    )
    with pytest.raises(IdentityKeysUnavailable):
        await verifier.verify(mint(RSA))
    assert h.causes() == ["jwks_transport", "keys_unavailable"]
    assert "ConnectError" not in h.log.getvalue()


@pytest.mark.asyncio
async def test_fr1_3_construction_fetches_nothing(h: Harness) -> None:
    """The API starts with the issuer down: nothing is fetched until the first request."""
    assert h.issuer.paths == []
    await h.verify(mint(RSA))
    await h.verify(mint(RSA))
    assert (h.issuer.discovery_hits, h.issuer.jwks_hits) == (1, 1)


# --- construction ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "field_name"),
    [
        ({"issuer": "http://issuer.test/realms/webblock"}, "issuer"),
        ({"issuer": "issuer.test"}, "issuer"),
        ({"audience": ""}, "audience"),
        ({"max_token_lifetime": timedelta(0)}, "max_token_lifetime"),
    ],
)
def test_construction_refuses_an_unusable_setting(changes: dict[str, Any], field_name: str) -> None:
    """TR-26: an issuer that is not https, no audience or no lifetime is refused, by field."""
    with pytest.raises(ValueError, match=field_name):
        JwtVerifier(config(**changes), logger=new_logger(io.StringIO(), logging.INFO))


# --- logs and metrics (decision 0024 G8) ----------------------------------------------------------


@pytest.mark.asyncio
async def test_g8_no_token_claim_or_key_reaches_the_log(h: Harness) -> None:
    """TR-33, decision 0024 G8: the identity's log lines hold fixed causes only."""
    subject = "agent:SUBJECT-MARKER"
    good = mint(RSA, claims(sub=subject, roles=["ROLE-MARKER"]))
    await h.verify(good)
    bad = mint(RSA, claims(sub=subject, iss="https://ISSUER-MARKER.test"))
    await h.refused(bad)
    await h.refused(mint(rsa_key("KID-MARKER", seed="b")))
    with pytest.raises(ActionDenied):
        await h.verify(mint(RSA, claims(sub=subject, user_id="USER-MARKER")))
    text = h.log.getvalue()
    for leak in (good, bad, "MARKER", RSA.jwk()["n"][:20], "issuer.test", USER_ID):
        assert leak not in text
    records = h.records()
    assert {r["msg"] for r in records} == {"token refused", "no user id"}
    assert all(r.get("component") == "identity" for r in records)


@pytest.mark.asyncio
async def test_g8_every_logged_cause_is_one_the_metrics_count(h: Harness) -> None:
    """TR-40, decision 0024 G8: a cause the metrics do not know would be counted as ``other``."""
    tokens = [
        mint(RSA, claims(typ="ID")),
        mint(ec_key(), claims()),
        mint(RSA, claims(user_id=None)),
        mint(RSA, claims(iat=int(T0) + 60)),
        "a.b",
    ]
    for token in tokens:
        with pytest.raises((Unauthenticated, ActionDenied)):
            await h.verify(token)
    h.issuer.status = 500
    h.clock.now += 3600  # past the refresh and the 30 s between fetches
    with pytest.raises(IdentityKeysUnavailable):
        await h.verify(mint(rsa_key("new-kid", seed="c")))
    causes = h.causes()
    assert len(causes) == len(tokens) + 2
    assert set(causes) <= AUTH_CAUSES | KEY_FETCH_CAUSES


@pytest.mark.asyncio
async def test_g8_the_librarys_other_records_keep_their_level(h: Harness) -> None:
    """TR-32, decision 0024 G8: the library's other records keep their level and message."""
    logging.getLogger("webblock_commons").warning("a library warning")
    logging.getLogger("webblock_commons").debug("a library detail")
    records = h.records()
    assert [(r["msg"], r["level"], r["component"]) for r in records] == [
        ("a library warning", "warning", "webblock_commons"),
        ("a library detail", "debug", "webblock_commons"),
    ]


def test_g8_detach_restores_the_library_logger() -> None:
    """TR-32, decision 0024 G8: attaching twice keeps one bridge; detaching restores the logger."""
    library = logging.getLogger("webblock_commons")
    before = (library.level, library.propagate, list(library.handlers))
    detach = attach_library_logs(new_logger(io.StringIO(), logging.INFO))
    again = attach_library_logs(new_logger(io.StringIO(), logging.INFO))
    assert len(library.handlers) == len(before[2]) + 1  # a second attach replaces the first
    assert (library.level, library.propagate) == (logging.DEBUG, False)
    again()
    detach()
    assert library.handlers == before[2]


# --- the key set's trust roots (TR-26; finding F-5 of H-V; decision 0024 G4) ------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("variable", ["SSL_CERT_FILE", "SSL_CERT_DIR"])
async def test_tr26_a_ca_named_by_the_environment_is_not_trusted_by_the_key_set_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    """TR-26, FR-1.3: the key set is fetched with the platform's roots only. SSL_CERT_FILE or
    SSL_CERT_DIR naming the issuer's CA does not make it trusted: the fetch fails closed and the
    token is ``IdentityKeysUnavailable`` (503), as before the library."""
    ca_dir = tmp_path / "ca-dir"
    ca_dir.mkdir()
    (ca_dir / TEST_CA_HASH_NAME).write_bytes(local_ca().ca_pem)
    trust = ca_dir / TEST_CA_HASH_NAME if variable == "SSL_CERT_FILE" else ca_dir
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(variable, str(trust))
    now = time.time()
    async with https_issuer(tmp_path, RSA.jwk(alg="RS256")) as server:
        settings = config(issuer=server.issuer)
        token = mint(RSA, claims(now=now, iss=server.issuer))
        # The neighbour: the standard library's default context trusts the issuer through the
        # variable, so the refusal below is the verifier's doing and not a broken fixture.
        lax = JwtVerifier(
            settings,
            logger=new_logger(io.StringIO(), logging.INFO),
            ssl_context=ssl.create_default_context(),
        )
        assert (await lax.verify(token)).user_id == UUID(USER_ID)
        hits = server.hits
        verifier = JwtVerifier(settings, logger=new_logger(io.StringIO(), logging.INFO))
        with pytest.raises(IdentityKeysUnavailable):
            await verifier.verify(token)
        assert server.hits == hits  # the handshake failed before any request


def test_tr26_a_platform_without_roots_refuses_the_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TR-26: with no compiled-in CA file or directory the verifier is refused at construction
    (the API does not start) rather than answering 503 forever."""
    missing = ssl.DefaultVerifyPaths(
        "/nonexistent/cert.pem",
        "/nonexistent/certs",
        "SSL_CERT_FILE",
        "/nonexistent/cert.pem",
        "SSL_CERT_DIR",
        "/nonexistent/certs",
    )
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: missing)
    with pytest.raises(ValueError, match="tls: the platform has no certificate roots"):
        JwtVerifier(config(), logger=new_logger(io.StringIO(), logging.INFO))


def test_tr26_a_context_that_skips_verification_is_refused() -> None:
    """TR-26: a test's own trust must still verify certificates and host names."""
    lax = ssl.create_default_context()
    lax.check_hostname = False
    lax.verify_mode = ssl.CERT_NONE
    with pytest.raises(ValueError, match="ssl_context must verify"):
        JwtVerifier(config(), logger=new_logger(io.StringIO(), logging.INFO), ssl_context=lax)
