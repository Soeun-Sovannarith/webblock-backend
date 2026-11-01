"""provisioning: runs sql/004_provisioning.sql.

Revision ID: 004
Revises: 003
"""
from migrations.sql_files import run_sql_file

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("004_provisioning.sql")


def downgrade() -> None:
    raise NotImplementedError("Migrations only go forward: write a new sql/ file that undoes the change.")
