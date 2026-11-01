"""The bearer-JWT verifier (decision 0024 G4 to G6). It implements the transports'
``TokenVerifier`` on top of web-block-commons' ``TokenVerifier``.

``verify`` returns the ``Principal`` a token authenticates, or raises one of three errors:

- ``Unauthenticated("token refused")`` for every refused token, whatever the cause (401, one
  message): the library's checks (G4), then Provisioning's own (``token.subject_of``, G5);
- ``IdentityKeysUnavailable`` when no key set recent enough can be had (503 identity_unavailable,
  never 401);
- ``ActionDenied`` for a token that passed both but has no valid ``user_id`` (403 ACCESS_DENIED,
  FR-2), before any store or Project call.

The library logs its own refusal causes; ``log_bridge`` turns them into the identity's events.
This module logs the causes of G5 and G6 the same way, never with the token, a claim value or a
key. Nothing is fetched at construction: the key set is fetched on the first request, so the API
starts with the issuer down and answers 503 until keys arrive. The fetch trusts the platform's
roots only (``tls.platform_roots``, TR-26), never a CA the process environment names.

The principal's subject is ``sub``; its scopes are the top-level ``roles`` (a list of text, else
none) and its user ID the ``user_id`` claim, both read by the library (TR-27).
"""

from __future__ import annotations

import ssl
import time
from collections.abc import Callable
from typing import Final

import httpx2
from webblock_commons.core import errors as library_errors
from webblock_commons.core.errors import WebBlockProblem
from webblock_commons.core.settings import Settings
from webblock_commons.core.user import user_from_claims
from webblock_commons.core.verifier import TokenVerifier

from provisioning.core.application import ActionDenied, Principal, Unauthenticated
from provisioning.infrastructure.config.identity import JWTConfig
from provisioning.infrastructure.identity.token import Refusal, subject_of
from provisioning.infrastructure.telemetry import Logger
from provisioning.infrastructure.tls import platform_roots

REFUSED: Final = "token refused"
SERVICE_NAME: Final = "provisioning"
"""The library's service name: the domain of a gRPC ``ErrorInfo`` it would write."""


class IdentityKeysUnavailable(Exception):
    """No key set recent enough to verify with: the issuer is down or answering garbage, and
    nothing usable is cached. It says nothing about the token; the transport answers 503
    identity_unavailable."""


class JwtVerifier:
    """Verifies bearer JWTs with web-block-commons. Construct it once per process; use it from one
    event loop."""

    __slots__ = ("_clock", "_log", "_max_lifetime", "_tokens")

    def __init__(
        self,
        config: JWTConfig,
        *,
        logger: Logger,
        clock: Callable[[], float] = time.time,
        ssl_context: ssl.SSLContext | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """``ValueError`` (naming the field, never the value) for an issuer that is not an
        absolute https URL, an empty audience, a lifetime that is not positive, a platform with no
        certificate roots, or an ``ssl_context`` that does not verify certificates and host names.

        The key set is fetched with ``ssl_context``, by default ``tls.platform_roots()``: the
        library's own default (the system trust store) lets ``SSL_CERT_FILE`` and ``SSL_CERT_DIR``
        widen trust, so the service passes its roots through the library's ``ssl_context``
        argument, which the library documents as test-only (decision 0024 G4). Only tests pass
        another context, or ``transport``, which replaces the network."""
        if config.max_token_lifetime.total_seconds() <= 0:
            raise ValueError("identity: max_token_lifetime must be positive")
        if ssl_context is None:
            ssl_context = platform_roots()
        elif ssl_context.verify_mode != ssl.CERT_REQUIRED or not ssl_context.check_hostname:
            raise ValueError("identity: ssl_context must verify certificates and host names")
        settings = Settings(
            issuer=config.issuer, audience=config.audience, service_name=SERVICE_NAME
        )
        self._tokens = TokenVerifier(
            settings, clock=clock, transport=transport, ssl_context=ssl_context
        )
        self._clock = clock
        self._max_lifetime = config.max_token_lifetime.total_seconds()
        self._log = logger

    def __repr__(self) -> str:
        return "JwtVerifier()"

    async def verify(self, raw: str) -> Principal:
        """The principal of ``raw`` (the token without "Bearer ")."""
        try:
            claims = await self._tokens.claims((f"Bearer {raw}",))
        except WebBlockProblem as problem:
            if problem.error.code == library_errors.IDENTITY_UNAVAILABLE.code:
                self._log.warning(
                    "identity keys unavailable",
                    component="identity",
                    auth_cause="keys_unavailable",
                )
                raise IdentityKeysUnavailable("identity keys unavailable") from None
            raise Unauthenticated(REFUSED) from None  # the library logged the cause
        try:
            subject = subject_of(claims, now=self._clock(), max_lifetime=self._max_lifetime)
        except Refusal as refusal:
            self._log.info(REFUSED, component="identity", auth_cause=refusal.cause)
            raise Unauthenticated(REFUSED) from None
        try:
            user = user_from_claims(claims)
        except WebBlockProblem:
            self._log.info("no user id", component="identity", auth_cause="user_id_missing")
            raise ActionDenied("no user_id") from None
        return Principal(subject=subject, scopes=user.roles, user_id=user.user_id)
