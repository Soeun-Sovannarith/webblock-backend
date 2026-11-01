"""Provisioning's part of the shared catalog (Notion 6 · Shared catalog; decision 0014
section 14): ``access_setup``, which creates each website's login role ``site_<hex>`` in the catalog
database and delivers its credential to the Secret ``catalog-runtime`` through the bootstrap
component.

- ``access_setup``: the create-or-verify rule and its order (Secret before password).
- ``site_access``: the catalog database side (``PostgresSiteAccess``), psycopg only, no string-built
  SQL.
- ``credential`` and ``scram``: the role name, the runtime credential and its SCRAM-SHA-256
  verifier.
"""

from provisioning.infrastructure.catalog.access_setup import (
    RUNTIME_SECRET_KEYS,
    AccessOutcome,
    CatalogAccessSetup,
    RuntimeEndpoint,
    RuntimeSecretSink,
    SiteAccess,
    runtime_secret_values,
)
from provisioning.infrastructure.catalog.credential import (
    RuntimeCredential,
    check_site_role_name,
    new_runtime_credential,
    site_role_name,
)
from provisioning.infrastructure.catalog.errors import (
    CatalogUnavailable,
    InvalidRuntimeCredential,
    InvalidSiteRoleName,
    RuntimeSecretMissing,
    SiteAccessInvalid,
)
from provisioning.infrastructure.catalog.scram import new_scram_verifier, scram_verifier
from provisioning.infrastructure.catalog.site_access import (
    AdminLogin,
    CatalogConnection,
    PostgresSiteAccess,
)

__all__ = [
    "RUNTIME_SECRET_KEYS",
    "AccessOutcome",
    "AdminLogin",
    "CatalogAccessSetup",
    "CatalogConnection",
    "CatalogUnavailable",
    "InvalidRuntimeCredential",
    "InvalidSiteRoleName",
    "PostgresSiteAccess",
    "RuntimeCredential",
    "RuntimeEndpoint",
    "RuntimeSecretMissing",
    "RuntimeSecretSink",
    "SiteAccess",
    "SiteAccessInvalid",
    "check_site_role_name",
    "new_runtime_credential",
    "new_scram_verifier",
    "runtime_secret_values",
    "scram_verifier",
    "site_role_name",
]
