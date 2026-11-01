"""The per-website login role and its runtime credential (Notion 6 · Shared catalog, decision
0014 section 14).

The role of website ``<id>`` is ``site_`` followed by the 32 lowercase hexadecimal digits of the ID.
Role names cannot be bound parameters, so every name is checked against ``^site_[0-9a-f]{32}$``
immediately before it is quoted into a statement with ``psycopg.sql.Identifier``.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from uuid import UUID

from provisioning.infrastructure.catalog.errors import InvalidSiteRoleName
from provisioning.infrastructure.catalog.scram import check_password

_SITE_ROLE = re.compile(r"site_[0-9a-f]{32}", re.ASCII)
# 32 random bytes, 256 bits: 43 unpadded base64url characters.
_PASSWORD_BYTES = 32
_REDACTED = "[REDACTED]"


def check_site_role_name(name: str) -> str:
    """``name`` when it is ``site_`` and 32 lowercase hex digits; otherwise InvalidSiteRoleName."""
    if not isinstance(name, str) or _SITE_ROLE.fullmatch(name) is None:
        raise InvalidSiteRoleName("not a site role name")
    return name


def site_role_name(project_id: UUID) -> str:
    """The checked role name of a project's website."""
    return check_site_role_name(f"site_{project_id.hex}")


@dataclass(frozen=True, slots=True)
class RuntimeCredential:
    """A project's site role and its password. The password never appears in ``repr`` or ``str``;
    ``reveal_password`` is called only to write the Secret and to log in once to check it."""

    project_id: UUID
    role: str
    _password: str = field(repr=False)

    def __post_init__(self) -> None:
        if check_site_role_name(self.role) != site_role_name(self.project_id):
            raise InvalidSiteRoleName("the credential's role is not its project's site role")
        check_password(self._password)

    def reveal_password(self) -> str:
        return self._password

    def __repr__(self) -> str:
        return (
            f"RuntimeCredential(project_id={self.project_id}, role={self.role}, "
            f"password={_REDACTED})"
        )

    __str__ = __repr__


def new_runtime_credential(project_id: UUID) -> RuntimeCredential:
    """A new credential of the project: its role and a fresh password from ``secrets``, never
    derived from a time, an ID or a counter."""
    return RuntimeCredential(
        project_id, site_role_name(project_id), secrets.token_urlsafe(_PASSWORD_BYTES)
    )
