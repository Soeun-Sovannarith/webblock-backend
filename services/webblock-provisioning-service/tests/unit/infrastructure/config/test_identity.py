"""The API's identity configuration (decision 0006 sections 5 and 7, decision 0024 G7; OD-05,
OD-15).

IDENTITY_MODE is ``dev-tokens`` or ``jwt`` in the prototype and only ``jwt`` in production. With
``jwt`` every key of the verifier (AUTH_ISSUER, an https URL; AUTH_AUDIENCE;
AUTH_MAX_TOKEN_LIFETIME) is required and has no default; with ``dev-tokens`` those keys are refused
by name when set, and with ``jwt`` the token file is. The keys whose values web-block-commons fixes
are refused by name in every profile and mode, and no key can turn off TLS verification. A reason
names the key and its rule, never the value.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from tests.unit.infrastructure.config.test_api import ENV, JWT, PRODUCTION

from provisioning.core.application import Action
from provisioning.core.application.shared.profile import Profile
from provisioning.infrastructure.config import APIConfig, LoadError, load_api
from provisioning.infrastructure.config.identity import (
    IDENTITY_JWT,
    IDENTITY_MODES,
    JWT_KEYS,
    RETIRED_KEYS,
    RETIRED_REASON,
    admin_only_scopes_reason,
)

MARKER = "VALUE-MARKER-9f1"

PROTOTYPE_JWT = (
    {k: v for k, v in ENV.items() if k != "AUTH_DEV_TOKENS_FILE"} | JWT | {"IDENTITY_MODE": "jwt"}
)


def _load(env: dict[str, str]) -> APIConfig:
    return load_api(env.get)


def _refused(env: dict[str, str]) -> LoadError:
    with pytest.raises(LoadError) as exc_info:
        _load(env)
    return exc_info.value


def _with(base: dict[str, str], **changes: str | None) -> dict[str, str]:
    env = dict(base)
    for key, value in changes.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def test_the_modes_of_each_profile() -> None:
    assert IDENTITY_MODES == {
        Profile.PROTOTYPE: ("dev-tokens", "jwt"),
        Profile.PRODUCTION: ("jwt",),
    }


def test_the_jwt_keys_are_the_ones_decision_0006_names() -> None:
    assert set(JWT_KEYS) == set(JWT)


def test_jwt_in_the_prototype_reads_every_value() -> None:
    c = _load(PROTOTYPE_JWT)
    assert c.identity.mode == IDENTITY_JWT
    assert c.identity.dev_tokens_file is None
    j = c.identity.jwt
    assert j is not None
    assert j.issuer == "https://issuer.example.test/realms/webblock"
    assert j.audience == "provisioning"
    assert j.max_token_lifetime == timedelta(minutes=15)


def test_dev_tokens_has_no_jwt_settings() -> None:
    c = _load(ENV)
    assert c.identity.mode == "dev-tokens"
    assert c.identity.jwt is None


def test_production_loads_with_jwt() -> None:
    c = _load(PRODUCTION)
    assert c.profile is Profile.PRODUCTION
    assert c.identity.mode == IDENTITY_JWT
    assert c.identity.jwt is not None
    assert c.identity.dev_tokens_file is None


@pytest.mark.parametrize("key", sorted(JWT))
def test_each_jwt_key_is_required_and_named(key: str) -> None:
    for base in (PROTOTYPE_JWT, PRODUCTION):
        for value in (None, ""):
            err = _refused(_with(base, **{key: value}))
            assert [(p.key, p.reason) for p in err.problems] == [(key, "is required")]


def test_a_missing_jwt_section_names_every_key() -> None:
    env = {k: v for k, v in PRODUCTION.items() if k not in JWT}
    assert _refused(env).keys() == tuple(sorted(JWT))


@pytest.mark.parametrize("key", sorted(JWT))
def test_dev_tokens_refuses_each_jwt_key_by_name(key: str) -> None:
    err = _refused({**ENV, key: JWT[key]})
    assert [(p.key, p.reason) for p in err.problems] == [
        (key, "must be unset unless IDENTITY_MODE is jwt")
    ]


def test_jwt_refuses_the_dev_token_file_by_name() -> None:
    err = _refused({**PROTOTYPE_JWT, "AUTH_DEV_TOKENS_FILE": "/etc/webblock/tokens.json"})
    assert [(p.key, p.reason) for p in err.problems] == [
        ("AUTH_DEV_TOKENS_FILE", "must be unset unless IDENTITY_MODE is dev-tokens")
    ]


def test_production_refuses_dev_tokens_as_a_stand_in() -> None:
    err = _refused({**PRODUCTION, "IDENTITY_MODE": "dev-tokens"})
    assert [(p.key, p.reason) for p in err.problems] == [
        (
            "IDENTITY_MODE",
            "must be jwt in production: dev-tokens is a prototype stand-in (OD-05)",
        )
    ]
    err = _refused({**PRODUCTION, "AUTH_DEV_TOKENS_FILE": "/etc/webblock/tokens.json"})
    assert err.keys() == ("AUTH_DEV_TOKENS_FILE",)
    assert err.problems[0].reason.startswith("must be unset in production: ")


def test_an_invalid_mode_names_the_mode_and_still_checks_the_profiles_keys() -> None:
    err = _refused(
        {**PRODUCTION, "IDENTITY_MODE": "oidc-" + MARKER, "AUTH_MAX_TOKEN_LIFETIME": "9d"}
    )
    assert err.keys() == ("AUTH_MAX_TOKEN_LIFETIME", "IDENTITY_MODE")
    assert MARKER not in str(err)


_URL_REASON = "must be an absolute https URL with a host, without credentials or a fragment"


@pytest.mark.parametrize(
    "value",
    [
        "http://issuer.example.test/realms/webblock",
        "HTTP://issuer.example.test/realms/webblock",
        "issuer.example.test/realms/webblock",
        "https:///realms/webblock",
        "https://user:pw-" + MARKER + "@issuer.example.test/realms/webblock",
        "https://issuer.example.test/realms/webblock#" + MARKER,
        "file:///etc/passwd",
        "https://issuer.example.test/realms/web block",
        " https://issuer.example.test/realms/webblock",
        "https://issuer.example.test/realms/\x07" + MARKER,
        "https://" + "a" * 2050 + ".test/",
    ],
)
def test_g7_the_issuer_must_be_an_https_url(value: str) -> None:
    """Decision 0024 G4, G7: the key set comes from the issuer's discovery document over https."""
    for base in (PROTOTYPE_JWT, PRODUCTION):
        err = _refused({**base, "AUTH_ISSUER": value})
        assert [(p.key, p.reason) for p in err.problems] == [("AUTH_ISSUER", _URL_REASON)]
        assert MARKER not in str(err)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("AUTH_MAX_TOKEN_LIFETIME", "999ms"),
        ("AUTH_MAX_TOKEN_LIFETIME", "24h1s"),
        ("AUTH_MAX_TOKEN_LIFETIME", "15"),
        ("AUTH_AUDIENCE", "   "),
        ("AUTH_AUDIENCE", "a" * 513),
        ("AUTH_AUDIENCE", "provisioning\x07" + MARKER),
    ],
)
def test_invalid_jwt_values_are_refused_by_key_and_never_echoed(key: str, value: str) -> None:
    err = _refused({**PRODUCTION, key: value})
    assert err.keys() == (key,)
    assert MARKER not in str(err)


@pytest.mark.parametrize("value", ["1s", "24h"])
def test_boundary_lifetimes_are_accepted(value: str) -> None:
    j = _load({**PRODUCTION, "AUTH_MAX_TOKEN_LIFETIME": value}).identity.jwt
    assert j is not None


def test_g7_the_retired_keys_are_the_ones_the_library_fixes() -> None:
    assert RETIRED_KEYS == (
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
    assert not set(RETIRED_KEYS) & set(JWT_KEYS)


@pytest.mark.parametrize("key", RETIRED_KEYS)
def test_g7_a_retired_key_is_refused_by_name_in_every_profile_and_mode(key: str) -> None:
    """Decision 0024 G7: a stale environment is caught at start-up, never silently ignored."""
    for base in (ENV, PROTOTYPE_JWT, PRODUCTION):
        err = _refused({**base, key: "x-" + MARKER})
        assert [(p.key, p.reason) for p in err.problems] == [(key, RETIRED_REASON)]
        assert MARKER not in str(err)
    assert RETIRED_REASON == "must be unset: web-block-commons fixes it (decision 0024 G7)"


# --- FR-50: production maps rollback and every other admin-only action to ADMIN ----------------

_ADMIN_ONLY = [a for a in Action if a.is_admin_only()]


def _scopes(changes: dict[Action, str]) -> str:
    """Production's AUTH_ACTION_SCOPES with ``changes`` applied."""
    pairs = (p.split("=", 1) for p in PRODUCTION["AUTH_ACTION_SCOPES"].split(","))
    scopes = {Action(name): scope for name, scope in pairs} | changes
    return ",".join(f"{a.value}={scopes[a]}" for a in Action)


def test_fr50_production_maps_rollback_to_admin() -> None:
    """FR-50: production loads when rollback_release and the other admin-only actions map to
    ADMIN."""
    scopes = _load(PRODUCTION).identity.action_scopes
    assert scopes[Action.ROLLBACK_RELEASE] == "ADMIN"
    assert {scopes[a] for a in _ADMIN_ONLY} == {"ADMIN"}


@pytest.mark.parametrize("action", _ADMIN_ONLY, ids=[a.value for a in _ADMIN_ONLY])
@pytest.mark.parametrize(
    "scope",
    ["USER", "admin", "ADMINS", "prov.rollback_release", "ADMIN" + MARKER],
    ids=["USER", "admin", "ADMINS", "another scope", "longer name"],
)
def test_fr50_production_refuses_an_admin_only_action_mapped_to_another_scope(
    action: Action, scope: str
) -> None:
    """FR-50.2: production refuses AUTH_ACTION_SCOPES that maps rollback_release (or another
    admin-only action) to anything but ADMIN; the reason names the action, never the scope."""
    err = _refused({**PRODUCTION, "AUTH_ACTION_SCOPES": _scopes({action: scope})})
    assert [(p.key, p.reason) for p in err.problems] == [
        ("AUTH_ACTION_SCOPES", admin_only_scopes_reason([action]))
    ]
    assert action.value in admin_only_scopes_reason([action])
    assert MARKER not in str(err)


def test_fr50_production_names_every_admin_only_action_not_mapped_to_admin() -> None:
    """FR-50.2: one problem names each admin-only action that is not mapped to ADMIN, in order."""
    changes = {Action.ROLLBACK_RELEASE: "USER", Action.REDEPLOY_RELEASE: "USER"}
    err = _refused({**PRODUCTION, "AUTH_ACTION_SCOPES": _scopes(changes)})
    assert [(p.key, p.reason) for p in err.problems] == [
        (
            "AUTH_ACTION_SCOPES",
            admin_only_scopes_reason([Action.ROLLBACK_RELEASE, Action.REDEPLOY_RELEASE]),
        )
    ]
    assert admin_only_scopes_reason([Action.ROLLBACK_RELEASE]) == (
        "must map each admin-only action to ADMIN in production (FR-50, decision 0023 G10); "
        "not ADMIN: rollback_release"
    )


def test_fr50_the_prototype_may_map_rollback_to_another_scope() -> None:
    """FR-50: the prototype keeps its own scope names; the gate still asks for the ADMIN role."""
    c = _load(ENV)
    assert c.identity.action_scopes[Action.ROLLBACK_RELEASE] == "prov.rollback_release"


def test_no_key_can_turn_off_tls_verification() -> None:
    """The verifier's trust is the system's: no configuration key names a CA file, an insecure
    flag or a proxy."""
    for key in JWT_KEYS:
        for word in ("INSECURE", "VERIFY", "TLS", "CA", "CERT", "PROXY", "SKIP"):
            assert word not in key.removeprefix("AUTH_").split("_"), key
