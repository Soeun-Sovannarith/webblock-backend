"""project: runs sql/001_project.sql.

Revision ID: 001
Revises: 000
"""
from migrations.sql_files import run_sql_file

revision = "001"
down_revision = "000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("001_project.sql")


def downgrade() -> None:
    raise NotImplementedError("Migrations only go forward: write a new sql/ file that undoes the change.")
