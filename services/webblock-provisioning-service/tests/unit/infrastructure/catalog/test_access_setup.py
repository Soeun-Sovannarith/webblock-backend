"""access_setup's create-or-verify decision and its order (decision 0011 section 5 and the G2
follow-up 1 ruling; decision 0014 section 14): the role is created, or a credential delivered, only
when the role is absent, or when the Secret is missing and no release is live; otherwise the role is
only verified. The Secret is always written before the role's password is set."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

import pytest

from provisioning.infrastructure.catalog import (
    RUNTIME_SECRET_KEYS,
    AccessOutcome,
    CatalogAccessSetup,
    CatalogUnavailable,
    RuntimeCredential,
    RuntimeEndpoint,
    RuntimeSecretMissing,
    SiteAccessInvalid,
    site_role_name,
)

PROJECT = UUID("0b7c2f43-5d5e-4f1e-9a43-0e1d5c1c7a11")
ROLE = site_role_name(PROJECT)
ENDPOINT = RuntimeEndpoint(
    host="webblock-catalog-db", port=5432, database="catalog", sslmode="disable"
)


@dataclass
class World:
    """The catalog and the cluster as access_setup sees them, with one ordered log of effects."""

    role: bool = False
    secret: bool = False
    password: str | None = None
    secret_values: dict[str, str] | None = None
    log: list[str] = field(default_factory=list)
    fail_secret_write: Exception | None = None
    fail_verify: Exception | None = None

    # SiteAccess
    async def role_exists(self, project_id: UUID) -> bool:
        assert project_id == PROJECT
        self.log.append("role_exists")
        return self.role

    async def set_site_role(self, credential: RuntimeCredential) -> bool:
        self.log.append("set_site_role")
        created = not self.role
        self.role = True
        self.password = credential.reveal_password()
        return created

    async def verify_site_role(self, project_id: UUID) -> None:
        assert project_id == PROJECT
        self.log.append("verify_site_role")
        if self.fail_verify is not None:
            raise self.fail_verify

    # RuntimeSecretSink
    async def runtime_secret_exists(self, project_id: UUID) -> bool:
        assert project_id == PROJECT
        self.log.append("runtime_secret_exists")
        return self.secret

    async def write_runtime_secret(self, project_id: UUID, values: Mapping[str, str]) -> None:
        assert project_id == PROJECT
        self.log.append("write_runtime_secret")
        if self.fail_secret_write is not None:
            raise self.fail_secret_write
        self.secret = True
        self.secret_values = dict(values)


def setup_for(world: World) -> CatalogAccessSetup:
    return CatalogAccessSetup(site_access=world, secrets=world, endpoint=ENDPOINT)


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.asyncio
async def test_an_absent_role_is_created_after_its_secret_is_written(live: bool) -> None:
    # G2 follow-up 1: "the role is absent" is its own reason to create, even with a live release.
    world = World(role=False, secret=live)

    assert await setup_for(world).run(PROJECT, has_live_release=live) is AccessOutcome.CREATED

    writes = [e for e in world.log if e in ("write_runtime_secret", "set_site_role")]
    assert writes == ["write_runtime_secret", "set_site_role"]
    assert world.secret_values is not None
    assert world.secret_values["PGPASSWORD"] == world.password


@pytest.mark.asyncio
async def test_the_secret_holds_the_six_keys_of_the_role_and_the_endpoint() -> None:
    world = World()

    await setup_for(world).run(PROJECT, has_live_release=False)

    assert world.secret_values is not None
    assert tuple(sorted(world.secret_values)) == tuple(sorted(RUNTIME_SECRET_KEYS))
    assert world.secret_values == {
        "PGHOST": "webblock-catalog-db",
        "PGPORT": "5432",
        "PGDATABASE": "catalog",
        "PGUSER": ROLE,
        "PGPASSWORD": world.password,
        "PGSSLMODE": "disable",
    }


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.asyncio
async def test_an_existing_role_with_its_secret_is_only_verified(live: bool) -> None:
    world = World(role=True, secret=True)

    assert await setup_for(world).run(PROJECT, has_live_release=live) is AccessOutcome.VERIFIED

    assert "write_runtime_secret" not in world.log
    assert "set_site_role" not in world.log
    assert world.log[-1] == "verify_site_role"


@pytest.mark.asyncio
async def test_a_missing_secret_without_a_live_release_gets_a_new_credential() -> None:
    world = World(role=True, secret=False)

    assert await setup_for(world).run(PROJECT, has_live_release=False) is AccessOutcome.DELIVERED

    writes = [e for e in world.log if e in ("write_runtime_secret", "set_site_role")]
    assert writes == ["write_runtime_secret", "set_site_role"]


@pytest.mark.asyncio
async def test_a_missing_secret_while_a_release_is_live_is_refused_and_nothing_changes() -> None:
    # A live release holds the current password; access_setup never rotates it.
    world = World(role=True, secret=False)

    with pytest.raises(RuntimeSecretMissing):
        await setup_for(world).run(PROJECT, has_live_release=True)

    assert "write_runtime_secret" not in world.log
    assert "set_site_role" not in world.log


@pytest.mark.asyncio
async def test_a_failed_secret_write_leaves_the_password_unset() -> None:
    world = World(role=False, fail_secret_write=CatalogUnavailable("cluster down"))

    with pytest.raises(CatalogUnavailable):
        await setup_for(world).run(PROJECT, has_live_release=False)

    assert "set_site_role" not in world.log
    assert world.password is None


@pytest.mark.asyncio
async def test_a_failed_verification_is_raised() -> None:
    world = World(role=True, secret=True, fail_verify=SiteAccessInvalid("not a member"))

    with pytest.raises(SiteAccessInvalid):
        await setup_for(world).run(PROJECT, has_live_release=False)


@pytest.mark.asyncio
async def test_every_new_credential_has_a_new_password() -> None:
    first, second = World(), World()

    await setup_for(first).run(PROJECT, has_live_release=False)
    await setup_for(second).run(PROJECT, has_live_release=False)

    assert first.password and second.password
    assert first.password != second.password


@pytest.mark.parametrize(
    ("field_name", "value"),
    [("host", ""), ("port", 0), ("port", 65536), ("database", ""), ("sslmode", "allow")],
)
def test_an_endpoint_the_site_could_not_use_is_refused(field_name: str, value: object) -> None:
    values: dict[str, object] = {
        "host": "webblock-catalog-db",
        "port": 5432,
        "database": "catalog",
        "sslmode": "disable",
    }
    values[field_name] = value
    with pytest.raises(ValueError, match=field_name):
        RuntimeEndpoint(**values)  # type: ignore[arg-type]  # values are exactly the four fields


def test_access_setup_delivers_exactly_the_keys_the_secret_renderer_accepts() -> None:
    from provisioning.infrastructure.kubernetes.render import (
        RUNTIME_SECRET_KEYS as RENDERED_KEYS,
    )

    assert RUNTIME_SECRET_KEYS == RENDERED_KEYS
