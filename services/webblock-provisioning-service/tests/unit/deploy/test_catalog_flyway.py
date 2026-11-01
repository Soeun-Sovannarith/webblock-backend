"""The prototype catalog's migration step as written, without a cluster (decision 0017 section 1).
``make proto-up`` proves it live (the Flyway history in ``catalog_flyway``).

- The Flyway image is pinned by digest at the version decision 0017 names.
- Provisioning keeps no copy of the catalog migrations; goose no longer touches the catalog.
- The migrator's password reaches the Flyway container only through the environment.
"""

import re
import shlex
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
VERSIONS = REPO / "deploy/prototype/catalog/versions.sh"
SCRIPT = REPO / "deploy/kind/proto-catalog.sh"


def _assignments(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        match = re.fullmatch(r"([A-Z_][A-Z0-9_]*)=(.*)", line.strip())
        if match:
            values[match.group(1)] = shlex.split(match.group(2))[0]
    return values


def _flyway_run(script: str) -> str:
    """The ``docker run`` of the Flyway step, its continuation lines joined."""
    joined = script.replace("\\\n", " ")
    runs = [line for line in joined.splitlines() if "docker run" in line and "PROTO_FLYWAY_IMAGE" in line]
    assert len(runs) == 1, runs
    return runs[0]


def test_the_flyway_image_is_pinned_by_digest_at_12_4_0() -> None:
    image = _assignments(VERSIONS)["PROTO_FLYWAY_IMAGE"]

    assert re.fullmatch(r"flyway/flyway:12\.4\.0-alpine@sha256:[0-9a-f]{64}", image), image


def test_provisioning_keeps_no_catalog_migrations_and_no_goose_for_the_catalog() -> None:
    script = SCRIPT.read_text()

    assert not (REPO / "deploy/prototype/catalog/migrations").exists()
    assert "$GOOSE" not in script
    assert "goose up" not in script
    assert "src/main/resources/db/migration/catalog" in script
    assert "scripts/db/catalog-bootstrap-roles.sql" in script
    assert "PROJECT_SERVICE_DIR" in script


def test_flyway_runs_as_the_migrator_in_the_owner_role_with_its_own_history_schema() -> None:
    run = _flyway_run(SCRIPT.read_text())

    for argument in (
        "--network kind",
        "-user=catalog_migrator",
        '-initSql="SET ROLE catalog_owner"',
        "-schemas=catalog_flyway",
        "-defaultSchema=catalog_flyway",
        "-placeholderReplacement=false",
        ":/flyway/sql:ro",
        " migrate",
    ):
        assert argument in run, argument


def test_the_password_reaches_flyway_through_the_environment_only() -> None:
    run = _flyway_run(SCRIPT.read_text())

    assert re.search(r"-e FLYWAY_PASSWORD(\s|$)", run), run
    assert "FLYWAY_PASSWORD=" not in run.split("docker run", 1)[1]
    assert "-password" not in run
    assert "PASSWORD" not in re.sub(r"FLYWAY_PASSWORD|CATALOG_MIGRATOR_PASSWORD", "", run)
