"""The API's identity configuration (decision 0006 sections 5 and 7; decision 0024).

IDENTITY_MODE selects how bearer tokens are verified:

- ``jwt``: the token check of ``web-block-commons`` (``infrastructure.identity``; decision 0024 G4
  to G6). Every key of ``JWT_KEYS`` is required and has no default: AUTH_ISSUER (the realm URL, an
  absolute https URL; the key set comes from its discovery document), AUTH_AUDIENCE and
  AUTH_MAX_TOKEN_LIFETIME (the lifetime cap Provisioning checks on top of the library). The
  library fixes the algorithm (RS256), the clock skew (30 s), the claims (``user_id``, top-level
  ``roles``) and the key set's limits, so their former keys (``RETIRED_KEYS``) are refused by name
  in every profile and mode: a stale environment is caught at start-up.
- ``dev-tokens``: the prototype's static tokens (AUTH_DEV_TOKENS_FILE), a stand-in refused in
  production. Each token entry names its own optional ``user_id``.

``IDENTITY_MODES`` says which mode each profile accepts. With one mode, the other mode's keys are
refused by name when set, so a setting that is not in force never seems to be. When IDENTITY_MODE
itself fails, the keys of the profile's first mode are read as usual (without that refusal), so one
run still names every problem.

AUTH_ACTION_SCOPES (the one role or scope each action requires) is read in both modes: a missing
mapping never means "permitted" (decision 0006 section 5). In production every admin-only action,
rollback_release included, must map to ``ADMIN`` (FR-50, decision 0023 G10); the gate asks for the
``ADMIN`` role on those actions whatever the mapping says.

Key names are decision 0006 section 7's. No Keycloak-specific name appears (decision 0006
section 5).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from provisioning.core.application import ADMIN_ROLE, Action, parse_action
from provisioning.core.application.shared.profile import Profile
from provisioning.core.domain import DomainError
from provisioning.infrastructure.config.loader import (
    Loader,
    has_control,
    plain_text,
    split_list,
)
from provisioning.infrastructure.config.profile import refuse_everywhere, refuse_in_production

IDENTITY_DEV_TOKENS: Final = "dev-tokens"
IDENTITY_JWT: Final = "jwt"

IDENTITY_MODES: Final[dict[Profile, tuple[str, ...]]] = {
    Profile.PROTOTYPE: (IDENTITY_DEV_TOKENS, IDENTITY_JWT),
    Profile.PRODUCTION: (IDENTITY_JWT,),
}
"""The identity modes each profile accepts; the first is the one whose keys are read when
IDENTITY_MODE itself is missing or invalid."""

JWT_KEYS: Final = ("AUTH_ISSUER", "AUTH_AUDIENCE", "AUTH_MAX_TOKEN_LIFETIME")
"""The verifier's keys, required with IDENTITY_MODE=jwt and refused otherwise."""

RETIRED_KEYS: Final = (
    "AUTH_JWKS_URL",
    "AUTH_ALLOWED_ALGS",
    "AUTH_CLOCK_SKEW",
    "AUTH_SCOPE_CLAIM",
    "IDENTITY_USER_ID_CLAIM",
    "AUTH_JWKS_FETCH_TIMEOUT",
    "AUTH_JWKS_MAX_BYTES",
    "AUTH_JWKS_REFRESH_INTERVAL",
    "AUTH_JWKS_MIN_REFRESH_INTERVAL",
    "AUTH_JWKS_MAX_STALE",
)
"""The former verifier keys whose values web-block-commons fixes (decision 0024 G7): refused in
every profile and mode."""
RETIRED_REASON: Final = "must be unset: web-block-commons fixes it (decision 0024 G7)"

DEV_TOKENS_FILE_KEY: Final = "AUTH_DEV_TOKENS_FILE"

ADMIN_ONLY_IN_PRODUCTION: Final = (
    "must map each admin-only action to ADMIN in production (FR-50, decision 0023 G10)"
)


def admin_only_scopes_reason(actions: Iterable[Action]) -> str:
    """The reason production refuses AUTH_ACTION_SCOPES: it names the admin-only actions not
    mapped to ADMIN, never their scopes."""
    return ADMIN_ONLY_IN_PRODUCTION + "; not ADMIN: " + ", ".join(a.value for a in actions)


STANDIN_DEV_TOKENS: Final = "the development tokens are a prototype stand-in (OD-05)"
DEV_TOKENS_IN_PRODUCTION: Final = (
    "must be jwt in production: dev-tokens is a prototype stand-in (OD-05)"
)

_URL_REASON: Final = "must be an absolute https URL with a host, without credentials or a fragment"


@dataclass(frozen=True, slots=True)
class JWTConfig:
    """The JWT verifier's settings (``infrastructure.identity.JwtVerifier``): the realm URL the
    library checks ``iss`` against and finds the key set from, this service's audience, and the
    longest token lifetime Provisioning accepts (decision 0024 G5)."""

    issuer: str
    audience: str
    max_token_lifetime: timedelta


@dataclass(frozen=True, slots=True)
class IdentityConfig:
    """``mode`` is dev-tokens or jwt. ``dev_tokens_file`` is the absolute path of the token file
    (dev-tokens only; its contents are never logged); ``jwt`` the verifier's settings (jwt only);
    ``action_scopes`` names the one scope each action requires."""

    mode: str
    dev_tokens_file: Path | None
    action_scopes: Mapping[Action, str]
    jwt: JWTConfig | None = None


def read_identity(loader: Loader, profile: Profile) -> IdentityConfig:
    """IDENTITY_MODE among ``IDENTITY_MODES[profile]`` and the keys of that mode."""
    modes = IDENTITY_MODES[profile]
    refuse_everywhere(loader, RETIRED_KEYS, RETIRED_REASON)
    value = loader.raw("IDENTITY_MODE")
    mode = ""
    if value is not None:
        if value in modes:
            mode = value
        elif profile is Profile.PRODUCTION and value == IDENTITY_DEV_TOKENS:
            loader.fail("IDENTITY_MODE", DEV_TOKENS_IN_PRODUCTION)
        else:
            loader.fail("IDENTITY_MODE", "must be one of " + ", ".join(modes))
    reading = mode or modes[0]
    tokens_file: Path | None = None
    jwt: JWTConfig | None = None
    if reading == IDENTITY_JWT:
        jwt = read_jwt(loader)
        if not refuse_in_production(loader, profile, DEV_TOKENS_FILE_KEY, STANDIN_DEV_TOKENS):
            if mode and loader.optional(DEV_TOKENS_FILE_KEY) is not None:
                loader.fail(DEV_TOKENS_FILE_KEY, "must be unset unless IDENTITY_MODE is dev-tokens")
    else:
        tokens_file = Path(loader.text(DEV_TOKENS_FILE_KEY, absolute_path))
        if mode:
            for key in JWT_KEYS:
                if loader.optional(key) is not None:
                    loader.fail(key, "must be unset unless IDENTITY_MODE is jwt")
    action_scopes = read_action_scopes(loader)
    if profile is Profile.PRODUCTION and action_scopes:
        not_admin = [a for a in Action if a.is_admin_only() and action_scopes[a] != ADMIN_ROLE]
        if not_admin:
            loader.fail("AUTH_ACTION_SCOPES", admin_only_scopes_reason(not_admin))
    return IdentityConfig(
        mode=mode,
        dev_tokens_file=tokens_file,
        action_scopes=action_scopes,
        jwt=jwt,
    )


def read_jwt(loader: Loader) -> JWTConfig:
    """The verifier's keys; every one is required (OD-15). The lifetime bounds are sanity limits
    that catch a typo or a unit mistake, not recommended values."""
    return JWTConfig(
        issuer=loader.text("AUTH_ISSUER", https_url),
        audience=loader.text("AUTH_AUDIENCE", plain_text(512)),
        max_token_lifetime=loader.duration(
            "AUTH_MAX_TOKEN_LIFETIME", timedelta(seconds=1), timedelta(hours=24)
        ),
    )


def https_url(value: str) -> str | None:
    """An absolute https URL with a host, without credentials, a fragment, whitespace or control
    characters, of at most 2048 bytes. The reason never contains the URL: it may hold
    credentials."""
    if (
        len(value.encode("utf-8", "surrogatepass")) > 2048
        or has_control(value)
        or any(c.isspace() for c in value)
        or "#" in value
    ):
        return _URL_REASON
    try:
        parts = urlsplit(value)
        port_ok = parts.port is None or parts.port > 0
    except ValueError:
        return _URL_REASON
    if (
        parts.scheme != "https"
        or not parts.hostname
        or "@" in parts.netloc
        or parts.fragment
        or not port_ok
    ):
        return _URL_REASON
    return None


def absolute_path(value: str) -> str | None:
    """An absolute file path of at most 4096 bytes without control characters."""
    reason = plain_text(4096)(value)
    if reason is not None:
        return reason
    if not value.startswith("/"):
        return "must be an absolute path"
    return None


def scope_token(value: str) -> bool:
    """One scope: 1 to 256 bytes of visible ASCII without space, quote, backslash, comma or
    equals sign (RFC 6749 section 3.3, narrowed so that the list syntax stays unambiguous)."""
    return 0 < len(value) <= 256 and all(" " < c < "\x7f" and c not in '"\\,=' for c in value)


def read_action_scopes(loader: Loader) -> Mapping[Action, str]:
    """``action=scope`` pairs; every action needs a non-empty scope: a missing
    mapping never means "permitted" (decision 0006 section 5)."""
    key = "AUTH_ACTION_SCOPES"
    value = loader.raw(key)
    if value is None:
        return {}
    items = split_list(value)
    if items is None:
        loader.fail(key, "must be action=scope pairs separated by commas, without empty elements")
        return {}
    scopes: dict[Action, str] = {}
    for item in items:
        name, sep, scope = item.partition("=")
        if not sep:
            loader.fail(key, "must be action=scope pairs separated by commas")
            return {}
        try:
            action = parse_action(name)
        except DomainError:
            loader.fail(key, "must name only known actions")
            return {}
        if action in scopes:
            loader.fail(key, "must name each action once")
            return {}
        if not scope_token(scope):
            loader.fail(
                key,
                "must give each action a non-empty scope of visible characters without spaces, "
                "commas or equals signs",
            )
            return {}
        scopes[action] = scope
    missing = [a.value for a in Action if a not in scopes]
    if missing:
        loader.fail(key, "must give a scope to every action; missing: " + ", ".join(missing))
        return {}
    return scopes
