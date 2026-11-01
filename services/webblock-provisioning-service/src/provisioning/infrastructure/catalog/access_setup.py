"""Provisioning's ``access_setup``, run by the bootstrap component (decision 0014 section 14;
decision 0013 section 9 K1): the project's catalog login role ``site_<hex>`` and the Secret
``catalog-runtime`` that the site's Deployment reads through ``envFrom``.

The create-or-verify rule (decision 0011 section 5 and the G2 follow-up 1 ruling of section 12):
- the role is absent: create it and deliver a new credential, whatever else holds;
- the role exists and so does the Secret: only verify the role;
- the role exists, the Secret is missing, no release is live: deliver a new credential (new
  password);
- the role exists, the Secret is missing, a release is live: ``RuntimeSecretMissing``, for an
  operator; the live release holds the current password, which access_setup never rotates.

A new credential is always written to the Secret **before** the role's password is set, so a crash
in between leaves a Secret and a role that the next run completes, never a role whose password
nobody holds.

The two collaborators are given by the host: ``PostgresSiteAccess`` (the catalog) and the bootstrap
component's ``KubernetesTenantBootstrap`` (the Secret). Whether a release is live comes from
Provisioning's own records (``project_hosting.live_release_id``), read by the host.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from provisioning.infrastructure.catalog.credential import (
    RuntimeCredential,
    new_runtime_credential,
)
from provisioning.infrastructure.catalog.errors import RuntimeSecretMissing
from provisioning.infrastructure.catalog.site_access import check_endpoint

RUNTIME_SECRET_KEYS = ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGSSLMODE")


class SiteAccess(Protocol):
    async def role_exists(self, project_id: UUID) -> bool: ...

    async def set_site_role(self, credential: RuntimeCredential) -> bool: ...

    async def verify_site_role(self, project_id: UUID) -> None: ...


class RuntimeSecretSink(Protocol):
    async def runtime_secret_exists(self, project_id: UUID) -> bool: ...

    async def write_runtime_secret(self, project_id: UUID, values: Mapping[str, str]) -> None: ...


class AccessOutcome(StrEnum):
    CREATED = "created"  # the role was created and its credential delivered
    DELIVERED = "delivered"  # the existing role got a new password, delivered to a new Secret
    VERIFIED = "verified"  # the role and its Secret existed; the role passed verification


@dataclass(frozen=True, slots=True)
class RuntimeEndpoint:
    """Where the site's pods reach the catalog (prototype: ``webblock-catalog-db:5432``)."""

    host: str
    port: int
    database: str
    sslmode: str

    def __post_init__(self) -> None:
        check_endpoint(self.host, self.port, self.database, self.sslmode)


def runtime_secret_values(
    endpoint: RuntimeEndpoint, credential: RuntimeCredential
) -> dict[str, str]:
    """The six keys of the Secret ``catalog-runtime``."""
    return {
        "PGHOST": endpoint.host,
        "PGPORT": str(endpoint.port),
        "PGDATABASE": endpoint.database,
        "PGUSER": credential.role,
        "PGPASSWORD": credential.reveal_password(),
        "PGSSLMODE": endpoint.sslmode,
    }


class CatalogAccessSetup:
    def __init__(
        self,
        *,
        site_access: SiteAccess,
        secrets: RuntimeSecretSink,
        endpoint: RuntimeEndpoint,
        new_credential: Callable[[UUID], RuntimeCredential] = new_runtime_credential,
    ) -> None:
        self._site_access = site_access
        self._secrets = secrets
        self._endpoint = endpoint
        self._new_credential = new_credential

    async def run(self, project_id: UUID, *, has_live_release: bool) -> AccessOutcome:
        """Create, deliver or verify, by the rule in the module docstring."""
        exists = await self._site_access.role_exists(project_id)
        if exists:
            if await self._secrets.runtime_secret_exists(project_id):
                await self._site_access.verify_site_role(project_id)
                return AccessOutcome.VERIFIED
            if has_live_release:
                raise RuntimeSecretMissing(
                    "the runtime secret is missing while a release is live; an operator must act"
                )
        credential = self._new_credential(project_id)
        await self._secrets.write_runtime_secret(
            project_id, runtime_secret_values(self._endpoint, credential)
        )
        await self._site_access.set_site_role(credential)
        return AccessOutcome.DELIVERED if exists else AccessOutcome.CREATED
