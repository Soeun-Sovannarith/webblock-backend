"""Alembic environment: the service's own database settings, an optional SET ROLE, one transaction per revision.

The revisions only run SQL files (migrations/sql_files.py), so there is no model metadata and no autogenerate.
"""
from logging.config import fileConfig
from alembic import context
from sqlalchemy import create_engine, text
from app.core.config import settings

# Always public.alembic_version: after SET ROLE ai_codegen the default search_path ("$user", public) would put it in the
# ai_codegen schema, where a plain login would not find it. public exists before the first migration; ai_codegen does not.
VERSION_SCHEMA = "public"

if context.config.config_file_name:
    fileConfig(context.config.config_file_name)


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL, for running it by hand (pgAdmin, psql)."""
    context.configure(url=settings.sqlalchemy_database_url.render_as_string(hide_password=True),
                      literal_binds=True, transaction_per_migration=True, version_table_schema=VERSION_SCHEMA)
    with context.begin_transaction():
        if settings.db_migration_role:
            context.execute(f"SET ROLE {_quote(settings.db_migration_role)}")
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(settings.sqlalchemy_database_url)
    with engine.connect() as connection:
        if settings.db_migration_role:
            # Session-wide, so every revision's transaction runs as this role (and it owns what they create).
            connection.execute(text(f"SET ROLE {connection.dialect.identifier_preparer.quote(settings.db_migration_role)}"))
            connection.commit()
        context.configure(connection=connection, transaction_per_migration=True, version_table_schema=VERSION_SCHEMA)
        context.run_migrations()


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
