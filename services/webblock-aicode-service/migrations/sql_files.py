"""Runs a file from sql/, the migrations' source of truth (docker compose's migrate service applies them too)."""
from pathlib import Path
from alembic import context, op

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def run_sql_file(name: str) -> None:
    sql = (SQL_DIR / name).read_text(encoding="utf-8")
    if context.is_offline_mode():
        context.get_context().impl.static_output(sql)
        return
    # Straight to psycopg, on Alembic's connection and inside its transaction, with no parameters: psycopg then sends
    # the file as one simple query, so several statements, DO $$ blocks, ::casts and % signs run as written.
    # (SQLAlchemy's text() reads ":name" as a bind parameter, and exec_driver_sql passes an empty parameter list,
    # which makes psycopg treat "%" as a placeholder.)
    op.get_bind().connection.driver_connection.execute(sql)
