"""The first insert of a project's hosting row, shared by the owner-status intake and the release
intake (FR-54, TR-53; owner rule of 27 October 2026: the database matches Notion "5 ·
Provisioning" exactly, so no unique index holds the sub-domain and the code checks it).

Inside the caller's transaction, before the insert:

1. ``pg_advisory_xact_lock(SUBDOMAIN_LOCK_CLASS, subdomain_lock_key(subdomain))``: inserts of one
   sub-domain run one at a time, until the transaction ends. The two-integer form keeps these keys
   apart from the one-bigint key of the bootstrap's session lock. Any login may take it; no grant.
2. When the caller's login may read ``subdomain`` (the hosting intake: SELECT on the table,
   migration 00022), it checks that no other project's row holds the sub-domain, else
   ``SubdomainTaken``. The transactions are READ COMMITTED, so the check after the lock sees every
   row a former holder of the lock committed. The release intake's login may read ``project_id``
   only (migration 00022), so it takes the lock and cannot check; the bootstrap, which may read
   ``subdomain`` (migration 00023), refuses a tenant to a project whose sub-domain another row
   holds (``tenant_projects``). No grant is added.
3. The insert, ``ON CONFLICT (project_id) DO NOTHING``: the same project's row already there is
   no refusal (Task 12 security re-review N1).
"""

from __future__ import annotations

import hashlib
from typing import Final
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import Insert
from sqlalchemy.ext.asyncio import AsyncConnection

from provisioning.core.application import SubdomainTaken
from provisioning.infrastructure.postgres.schema import project_hosting

SUBDOMAIN_LOCK_CLASS: Final = 0x53554244
"""The first key of the two-integer advisory lock: "SUBD" in ASCII."""

_LOCK_SQL: Final = (
    "SELECT pg_advisory_xact_lock(CAST(:lock_class AS integer), CAST(:lock_key AS integer))"
)
_LOCK: Final = text(_LOCK_SQL)


def subdomain_lock_key(subdomain: str) -> int:
    """The second key: the first four bytes of the sub-domain's SHA-256, a signed 32-bit integer,
    the same in every process. Two sub-domains that share a key only wait for each other."""
    return int.from_bytes(hashlib.sha256(subdomain.encode()).digest()[:4], "big", signed=True)


async def lock_subdomain(conn: AsyncConnection, subdomain: str) -> None:
    """Hold the sub-domain's lock until ``conn``'s transaction ends."""
    await conn.execute(
        _LOCK, {"lock_class": SUBDOMAIN_LOCK_CLASS, "lock_key": subdomain_lock_key(subdomain)}
    )


async def insert_hosting_row(
    conn: AsyncConnection,
    stmt: Insert,
    project_id: UUID,
    subdomain: str,
    *,
    check_holder: bool,
) -> bool:
    """Run ``stmt``, the insert of ``project_id``'s hosting row with ``subdomain`` and ``ON
    CONFLICT (project_id) DO NOTHING``, under the sub-domain's lock. True when it inserted the
    row, False when the project has a row already. With ``check_holder`` (only for a login that
    may read ``subdomain``), ``SubdomainTaken`` when another project's row holds it; its message
    never names the sub-domain."""
    await lock_subdomain(conn, subdomain)
    if check_holder:
        held = await conn.execute(
            select(project_hosting.c.project_id)
            .where(
                project_hosting.c.subdomain == subdomain,
                project_hosting.c.project_id != project_id,
            )
            .limit(1)
        )
        if held.first() is not None:
            raise SubdomainTaken(
                f"the sub-domain of project {project_id} belongs to another project"
            )
    result = await conn.execute(stmt)
    return result.rowcount == 1
