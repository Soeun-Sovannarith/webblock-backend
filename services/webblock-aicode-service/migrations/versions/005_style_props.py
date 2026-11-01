"""style props: runs sql/005_style_props.sql.

Revision ID: 005
Revises: 004
"""
from migrations.sql_files import run_sql_file

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("005_style_props.sql")


def downgrade() -> None:
    raise NotImplementedError("Migrations only go forward: write a new sql/ file that undoes the change.")
