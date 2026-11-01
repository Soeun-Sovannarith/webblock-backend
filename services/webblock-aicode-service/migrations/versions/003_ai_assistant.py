"""ai assistant: runs sql/003_ai_assistant.sql.

Revision ID: 003
Revises: 002
"""
from migrations.sql_files import run_sql_file

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("003_ai_assistant.sql")


def downgrade() -> None:
    raise NotImplementedError("Migrations only go forward: write a new sql/ file that undoes the change.")
