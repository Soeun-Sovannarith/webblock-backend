"""Alembic's revisions mirror sql/: one revision per file, in file order, each running its own file. No database."""
from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parent.parent
SQL_FILES = sorted(p.name for p in (ROOT / "sql").glob("*.sql"))


def _revisions():
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    return script, list(reversed(list(script.walk_revisions())))  # oldest first


def test_every_sql_file_has_one_revision_in_order():
    script, revisions = _revisions()
    assert [r.revision for r in revisions] == [name.split("_", 1)[0] for name in SQL_FILES]
    assert script.get_heads() == [SQL_FILES[-1].split("_", 1)[0]]
    for previous, revision in zip([None, *revisions[:-1]], revisions, strict=True):
        assert revision.down_revision == (previous.revision if previous else None)


def test_each_revision_runs_its_own_sql_file():
    _, revisions = _revisions()
    for revision, name in zip(revisions, SQL_FILES, strict=True):
        assert f'run_sql_file("{name}")' in Path(revision.path).read_text(encoding="utf-8"), revision.path
