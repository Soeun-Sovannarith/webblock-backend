"""reset legacy: runs sql/000_reset_legacy.sql.

Revision ID: 000
Revises: 
"""
from migrations.sql_files import run_sql_file

revision = "000"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("000_reset_legacy.sql")


def downgrade() -> None:
    raise NotImplementedError("Migrations only go forward: write a new sql/ file that undoes the change.")
