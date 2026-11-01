"""The drift test of ``infrastructure/postgres/schema.py`` (decision 0014 section 11).

Every ``Table`` of the mirror is compared with the catalogue of a database migrated to the latest
migration: the set of tables, and per table the columns in order with their types, nullability and
enum labels. Any difference fails. The goose migrations are the only schema source; the mirror
follows them.
"""

from __future__ import annotations

import re

from sqlalchemy import CHAR, VARCHAR, BigInteger, Integer, Table, Text, Uuid
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, TIMESTAMP
from tests.integration._pg import Database, q

from provisioning.infrastructure.postgres import schema


def _catalogue_type(text: str) -> str:
    """format_type() text in the spelling SQLAlchemy compiles."""
    if m := re.fullmatch(r"character varying\((\d+)\)", text):
        return f"VARCHAR({m[1]})"
    if m := re.fullmatch(r"character\((\d+)\)", text):
        return f"CHAR({m[1]})"
    if m := re.fullmatch(r"(provisioning(?:_ops)?\.\w+)\[]", text):
        return f"{m[1]}[]"
    return {
        "uuid": "UUID",
        "bigint": "BIGINT",
        "integer": "INTEGER",
        "text": "TEXT",
        "timestamp with time zone": "TIMESTAMP WITH TIME ZONE",
    }.get(text, text)


def _mirror_type(table: Table, column: str) -> str:
    """The mirror's column type in the same spelling; an unknown type class is its own name."""
    col_type = table.c[column].type
    if isinstance(col_type, ARRAY) and isinstance(col_type.item_type, ENUM):
        return f"{col_type.item_type.schema}.{col_type.item_type.name}[]"
    if isinstance(col_type, ENUM):
        return f"{col_type.schema}.{col_type.name}"
    if isinstance(col_type, CHAR):  # before String: CHAR is a String
        return f"CHAR({col_type.length})"
    if isinstance(col_type, VARCHAR):
        return f"VARCHAR({col_type.length})"
    if isinstance(col_type, TIMESTAMP):
        zone = "WITH" if col_type.timezone else "WITHOUT"
        return f"TIMESTAMP {zone} TIME ZONE"
    for kind, name in (
        (Uuid, "UUID"),
        (BigInteger, "BIGINT"),
        (Integer, "INTEGER"),
        (Text, "TEXT"),
    ):
        if isinstance(col_type, kind):
            return name
    return type(col_type).__name__


def _catalogue(db: Database) -> dict[str, list[tuple[str, str, bool]]]:
    with db.admin() as conn:
        found = conn.execute(
            q(
                "SELECT n.nspname || '.' || c.relname, a.attname,"
                " format_type(a.atttypid, a.atttypmod), NOT a.attnotnull"
                " FROM pg_attribute AS a JOIN pg_class AS c ON c.oid = a.attrelid"
                " JOIN pg_namespace AS n ON n.oid = c.relnamespace"
                " WHERE n.nspname IN ('provisioning', 'provisioning_ops') AND c.relkind = 'r'"
                " AND a.attnum > 0 AND NOT a.attisdropped"
                " ORDER BY 1, a.attnum"
            )
        ).fetchall()
    out: dict[str, list[tuple[str, str, bool]]] = {}
    for table, column, type_text, nullable in found:
        out.setdefault(table, []).append((column, _catalogue_type(type_text), nullable))
    return out


def _enum_labels(db: Database) -> dict[str, list[str]]:
    with db.admin() as conn:
        found = conn.execute(
            q(
                "SELECT n.nspname || '.' || t.typname,"
                " array_agg(e.enumlabel::text ORDER BY e.enumsortorder)"
                " FROM pg_type AS t JOIN pg_enum AS e ON e.enumtypid = t.oid"
                " JOIN pg_namespace AS n ON n.oid = t.typnamespace"
                " WHERE n.nspname IN ('provisioning', 'provisioning_ops')"
                " GROUP BY 1"
            )
        ).fetchall()
    return {name: list(labels) for name, labels in found}


def _mirror() -> dict[str, list[tuple[str, str, bool]]]:
    out: dict[str, list[tuple[str, str, bool]]] = {}
    for table in schema.metadata.tables.values():
        out[f"{table.schema}.{table.name}"] = [
            (c.name, _mirror_type(table, c.name), bool(c.nullable)) for c in table.columns
        ]
    return out


def test_the_mirror_matches_the_migrated_catalogue(db: Database) -> None:
    assert _mirror() == _catalogue(db)


def test_the_mirror_enum_labels_match_the_catalogue(db: Database) -> None:
    labels = _enum_labels(db)
    mirrored: dict[str, list[str]] = {}
    for table in schema.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, ENUM):
                mirrored[f"{column.type.schema}.{column.type.name}"] = list(column.type.enums)
    assert mirrored == labels


def test_a_drifted_mirror_is_detected(db: Database) -> None:
    """The comparison is not vacuous: one changed width or one missing column is a difference."""
    catalogue = _catalogue(db)
    drifted = _mirror()
    drifted["provisioning.commands"] = [
        (name, "VARCHAR(254)" if name == "idempotency_key" else t, n)
        for name, t, n in drifted["provisioning.commands"]
    ]
    assert drifted != catalogue
    missing = _mirror()
    missing["provisioning.releases"] = missing["provisioning.releases"][:-1]
    assert missing != catalogue
