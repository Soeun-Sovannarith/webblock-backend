"""catalog hardening: runs sql/008_catalog_hardening.sql.

Revision ID: 008
Revises: 007
"""
from migrations.sql_files import run_sql_file

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("008_catalog_hardening.sql")


def downgrade() -> None:
    raise NotImplementedError("Migrations only go forward: write a new sql/ file that undoes the change.")
