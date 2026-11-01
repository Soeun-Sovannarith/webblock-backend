"""ai codegen: runs sql/002_ai_codegen.sql.

Revision ID: 002
Revises: 001
"""
from migrations.sql_files import run_sql_file

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("002_ai_codegen.sql")


def downgrade() -> None:
    raise NotImplementedError("Migrations only go forward: write a new sql/ file that undoes the change.")
