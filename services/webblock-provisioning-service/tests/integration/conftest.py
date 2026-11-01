"""Fixtures of the PostgreSQL 18 suite (prototype task B2, decision 0014).

One ``postgres:18`` container per session, started on first use. Every test gets its own database
copied from a template that was migrated once. A missing Docker, image or server fails the run: this
folder is a no-skip suite (tests/conftest.py).

Fixtures:

- ``harness``: the session's :class:`Harness` (roles created, server checked).
- ``new_db``: factory ``new_db(version=None)``; the database is dropped at the end of the test.
- ``db``: a database at the latest migration.
- ``new_empty_db``: factory for a database with no migration applied.

The leftover check runs when the session ends: it fails if a database or role this run created is
still there.
"""

from collections.abc import Callable, Generator

import pytest
from tests.integration._pg import Database, Harness, start_server

type DbFactory = Callable[[int | None], Database]
type EmptyDbFactory = Callable[[], Database]


@pytest.fixture(scope="session")
def harness() -> Generator[Harness]:
    try:
        container, server = start_server()
    except RuntimeError as exc:
        pytest.fail(str(exc), pytrace=False)
    h = Harness(server)
    try:
        h.bootstrap()
        yield h
        # Everything the tests created must be gone before the leftover check; a test that failed
        # between creating and dropping is reported here instead of passing quietly.
        h.drop_templates()
        left = h.leftovers()
        h.drop_all()
        if left:
            pytest.fail("the run left behind: " + ", ".join(left), pytrace=False)
    finally:
        container.stop()


@pytest.fixture
def new_db(harness: Harness) -> Generator[DbFactory]:
    created: list[Database] = []

    def make(version: int | None = None) -> Database:
        database = harness.new_database(version)
        created.append(database)
        return database

    yield make
    for database in created:
        harness.drop_database(database)


@pytest.fixture
def new_empty_db(harness: Harness) -> Generator[EmptyDbFactory]:
    created: list[Database] = []

    def make() -> Database:
        database = harness.new_empty_database()
        created.append(database)
        return database

    yield make
    for database in created:
        harness.drop_database(database)


@pytest.fixture
def db(new_db: DbFactory) -> Database:
    return new_db(None)
