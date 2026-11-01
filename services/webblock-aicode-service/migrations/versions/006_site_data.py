"""site data: runs sql/006_site_data.sql.

Revision ID: 006
Revises: 005
"""
from migrations.sql_files import run_sql_file

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("006_site_data.sql")


def downgrade() -> None:
    raise NotImplementedError("Migrations only go forward: write a new sql/ file that undoes the change.")
