"""The PostgreSQL stores (prototype task B3; decision 0014 section 11): SQLAlchemy Core 2.1 on the
psycopg driver, one ``AsyncEngine`` per database role login.

- :mod:`.schema`: ``Table`` objects mirroring the migrated revision 19 schema (never creating it);
- :mod:`.engine`: engines with a bounded pool and ``hide_parameters=True``;
- :mod:`.tx`: bounded transactions with local timeouts and a shielded commit;
- :mod:`.errors`: driver errors to typed errors, by SQLSTATE and constraint name, ``from None``;
- :mod:`.rows`: rows to validated domain values;
- :mod:`.fenced`: the three checks of every fenced worker write, for the deploy and routing stores;
- the stores: acceptance (and its dispatch hook), reads, jobs and fenced writes, release intake,
  and the hosting-command intake.

The build stores are not re-exported: :mod:`.build_store` (``PgBuildStore``,
``PgBuildHintListener``) is imported by its module name, by the build worker alone, and
:mod:`.build_requests` (``PgBuildRequests``) by the API, so the API process never links the build
store (finding F-6 of H-V; the import-linter contract ``api-no-build-system``).

Importing this package opens no connection, file or thread.
"""

from provisioning.infrastructure.postgres.acceptance_store import (
    RACED_INDEXES,
    PgAcceptanceStore,
    insert_dispatch,
)
from provisioning.infrastructure.postgres.engine import (
    EngineSettings,
    InvalidEngineSettings,
    database_url,
    open_engine,
)
from provisioning.infrastructure.postgres.errors import (
    DatabaseError,
    DatabaseUnreachable,
    InvalidArgument,
    constraint_of,
    is_unique_violation,
    map_db_error,
)
from provisioning.infrastructure.postgres.fenced import (
    DispatchRow,
    FencedRows,
    finish_dispatch,
    load_fenced,
    write_command,
    write_release,
)
from provisioning.infrastructure.postgres.hosting_intake import HostingIntakeStore
from provisioning.infrastructure.postgres.intake_store import PgReleaseIntakeStore
from provisioning.infrastructure.postgres.job_store import STALE_CANDIDATE_CODE, PgJobStore
from provisioning.infrastructure.postgres.read_store import PgReadStore
from provisioning.infrastructure.postgres.tx import InvalidTimeouts, Timeouts, Transactions

__all__ = [
    "RACED_INDEXES",
    "STALE_CANDIDATE_CODE",
    "DatabaseError",
    "DatabaseUnreachable",
    "DispatchRow",
    "EngineSettings",
    "FencedRows",
    "HostingIntakeStore",
    "InvalidArgument",
    "InvalidEngineSettings",
    "InvalidTimeouts",
    "PgAcceptanceStore",
    "PgJobStore",
    "PgReadStore",
    "PgReleaseIntakeStore",
    "Timeouts",
    "Transactions",
    "constraint_of",
    "database_url",
    "finish_dispatch",
    "insert_dispatch",
    "is_unique_violation",
    "load_fenced",
    "map_db_error",
    "open_engine",
    "write_command",
    "write_release",
]
