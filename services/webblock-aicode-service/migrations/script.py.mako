<% sql_file = up_revision + "_" + message.lower().replace(" ", "_") + ".sql" %>\
"""${message}: runs sql/${sql_file}.

Write the SQL in that file: idempotent and schema-qualified (.claude/rules/sql-migrations.md).

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""
from migrations.sql_files import run_sql_file

revision = "${up_revision}"
down_revision = ${'"%s"' % down_revision if down_revision else "None"}
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("${sql_file}")


def downgrade() -> None:
    raise NotImplementedError("Migrations only go forward: write a new sql/ file that undoes the change.")
