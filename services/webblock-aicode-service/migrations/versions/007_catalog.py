"""catalog: runs sql/007_catalog.sql.

Revision ID: 007
Revises: 006
"""
from migrations.sql_files import run_sql_file

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("007_catalog.sql")


def downgrade() -> None:
    raise NotImplementedError("Migrations only go forward: write a new sql/ file that undoes the change.")
