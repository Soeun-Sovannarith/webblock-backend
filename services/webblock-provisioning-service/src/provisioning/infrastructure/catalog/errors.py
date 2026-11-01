"""The closed set of errors of access_setup (decision 0011 sections 5 and 11, decision 0014 section
14). Messages are fixed text: never a password, a host, a role's driver message or a statement."""

from __future__ import annotations


class InvalidRuntimeCredential(ValueError):
    """A password the SCRAM verifier refuses (empty, or not printable ASCII), or weak SCRAM
    settings. The message never contains the password."""


class InvalidSiteRoleName(ValueError):
    """Not ``site_`` followed by 32 lowercase hexadecimal digits, or not this project's role."""


class SiteAccessInvalid(Exception):
    """The project's site role exists but fails verification: not held by this administrator, not a
    login member of ``catalog_site`` only, cannot log in, or reads rows of another website.
    Permanent (``access_setup_invalid``)."""


class CatalogUnavailable(Exception):
    """The catalog database did not answer or refused a statement. Transient
    (``dependency_unavailable``). Carries at most the SQLSTATE."""


class RuntimeSecretMissing(Exception):
    """The role exists, its Secret ``catalog-runtime`` is missing, and a release is live: the live
    release holds the current password, so access_setup does not rotate it. Permanent
    (``runtime_secret_missing``), for an operator."""
