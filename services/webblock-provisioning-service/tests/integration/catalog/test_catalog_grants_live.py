"""The catalog grants of decision 0014 section 16 items 6 to 8, on the prototype stack's catalog
database (the Project service's catalog migration V4, decision 0017 section 1). Marked
``catalog_live`` by directory; a skip is a failure. They need ``make proto-up``.

- ``catalog_platform`` can ``UPDATE catalog.websites ... WHERE website_id = ...`` (the WHERE clause
  reads the column, which needs SELECT) and read ``catalog.business_types``.
- A site role cannot ``CREATE TEMP TABLE`` ("no DDL"): TEMP is revoked from PUBLIC on the database.
"""

import secrets
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from tests.integration.catalog.conftest import Live


@pytest.mark.asyncio
async def test_catalog_platform_updates_websites_by_id_and_reads_business_types(
    live: Live,
) -> None:
    platform = await live.connect(*live.tool.login("PLATFORM"))
    async with platform:
        cursor = await platform.execute("SELECT count(*) FROM catalog.business_types")
        row = await cursor.fetchone()
        assert row is not None and row[0] > 0
        # No such website: the statement is allowed and changes nothing. Without SELECT on
        # catalog.websites it fails with InsufficientPrivilege instead.
        cursor = await platform.execute(
            "UPDATE catalog.websites SET status = status WHERE website_id = %s", (uuid4(),)
        )
        assert cursor.rowcount == 0


@pytest.mark.asyncio
async def test_a_site_role_cannot_create_a_temporary_table(live: Live) -> None:
    name = f"site_grants_{secrets.token_hex(6)}"
    password = secrets.token_urlsafe(24)
    admin = await live.connect(*live.tool.login("ACCESS_ADMIN"))
    async with admin:
        await admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE catalog_site").format(
                sql.Identifier(name), sql.Literal(password)
            )
        )
        try:
            site = await live.connect(name, password)
            async with site:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    await site.execute("CREATE TEMP TABLE grants_probe (i integer)")
        finally:
            await admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(name)))
