"""SQL safety of the PostgreSQL stores (the owner's rule; proto-sql-safety.md rules 6 and 7;
decision 0014 section 11).

Every module under src/provisioning/infrastructure/postgres/ is parsed with ``ast`` and must not
contain:

- a ``text(...)`` or ``literal_column(...)`` call whose first argument is not the name of a
  module-level string constant; ``exec_driver_sql(...)`` at all;
- an f-string, a ``%`` or ``+`` expression, or a ``.format`` call inside such a call;
- a ``literal(...)`` call with anything but literal constants (no runtime data);
- an ``execute``/``exec_driver_sql``/``executemany`` call whose statement is a string built at
  runtime (an f-string, ``%``, ``+`` or ``.format``) or a bare string;
- ``create_all``, ``drop_all`` or ``reflect`` (the migrations are the only schema source);
- ``create_async_engine``/``create_engine`` without ``hide_parameters=True``;
- ``cast(LiteralString, ...)`` or a ``type: ignore`` on a line that executes SQL.

The planted-violation cases feed small modules to the same scanner and must each be reported, so
the scan is not vacuous.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

STORES = (
    Path(__file__).resolve().parents[2] / "src" / "provisioning" / "infrastructure" / "postgres"
)

TEXT_CALLS = frozenset({"text", "literal_column"})
EXECUTE_CALLS = frozenset({"execute", "executemany", "stream", "scalar", "scalars"})
SCHEMA_CALLS = frozenset({"create_all", "drop_all", "reflect"})
ENGINE_CALLS = frozenset({"create_async_engine", "create_engine"})


def _name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _module_constants(tree: ast.Module) -> set[str]:
    """Names bound once at module level to a plain string literal."""
    found: set[str] = set()
    for node in tree.body:
        value: ast.expr | None = None
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            value, targets = node.value, list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            value, targets = node.value, [node.target]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found.update(t.id for t in targets if isinstance(t, ast.Name))
    return found


def _built_string(node: ast.expr) -> bool:
    """An expression that builds a string at runtime."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.JoinedStr):
            return True
        if isinstance(sub, ast.BinOp) and isinstance(sub.op, (ast.Mod, ast.Add)):
            return True
        if isinstance(sub, ast.Call) and _name(sub.func) == "format":
            return True
    return False


def _text_expression(node: ast.expr) -> bool:
    """A statement argument that is SQL text: a string literal, an f-string, a ``.format`` call,
    or ``%``/``+`` with text on one side. Column arithmetic such as ``col + 1`` is not text."""
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.Call):
        return _name(node.func) == "format"
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        return _text_expression(node.left) or _text_expression(node.right)
    return False


def violations(source: str, filename: str = "<planted>") -> list[str]:
    """Every rule violation in one module's source, as ``file:line: reason``."""
    tree = ast.parse(source, filename=filename)
    constants = _module_constants(tree)
    lines = source.splitlines()
    found: list[str] = []

    def report(node: ast.AST, reason: str) -> None:
        found.append(f"{filename}:{getattr(node, 'lineno', 0)}: {reason}")

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _name(node.func)
        if name == "exec_driver_sql":
            report(node, "exec_driver_sql is not used")
        if name in TEXT_CALLS:
            first = node.args[0] if node.args else None
            if not (isinstance(first, ast.Name) and first.id in constants):
                report(node, f"{name}() takes only a module-level constant")
            if any(_built_string(a) for a in node.args):
                report(node, f"{name}() receives a string built at runtime")
        if name == "literal" and not all(isinstance(a, ast.Constant) for a in node.args):
            report(node, "literal() takes no runtime data")
        if name in EXECUTE_CALLS and node.args:
            if _text_expression(node.args[0]):
                report(node, f"{name}() receives SQL text")
            line = lines[node.lineno - 1] if node.lineno - 1 < len(lines) else ""
            if "type: ignore" in line:
                report(node, f"type: ignore on a {name}() line")
        if name in SCHEMA_CALLS:
            report(node, f"{name}() changes or reads the schema at runtime")
        if name in ENGINE_CALLS:
            hidden = any(
                k.arg == "hide_parameters"
                and isinstance(k.value, ast.Constant)
                and k.value.value is True
                for k in node.keywords
            )
            if not hidden:
                report(node, f"{name}() without hide_parameters=True")
        if (
            name == "cast"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "LiteralString"
        ):
            report(node, "cast(LiteralString, ...)")
    return found


def store_modules() -> list[Path]:
    return sorted(STORES.rglob("*.py"))


def test_the_store_package_is_scanned() -> None:
    names = {p.name for p in store_modules()}
    assert {"tx.py", "acceptance_store.py", "job_store.py", "engine.py", "schema.py"} <= names


def test_the_stores_have_no_sql_safety_violation() -> None:
    found: list[str] = []
    for path in store_modules():
        found += violations(path.read_text(encoding="utf-8"), str(path))
    assert found == []


PLANTED = [
    pytest.param(
        'from sqlalchemy import text\n\ndef q(x: str) -> object:\n    return text(f"SELECT {x}")\n',
        "text() takes only a module-level constant",
        id="f-string-text",
    ),
    pytest.param(
        'from sqlalchemy import text\nQ = "SELECT %s"\n\ndef q(x: str) -> object:\n'
        "    return text(Q % x)\n",
        "text() receives a string built at runtime",
        id="percent-text",
    ),
    pytest.param(
        'from sqlalchemy import text\n\ndef q(x: str) -> object:\n    return text("SELECT " + x)\n',
        "text() receives a string built at runtime",
        id="concat-text",
    ),
    pytest.param(
        "from sqlalchemy import literal_column\n\ndef q(x: str) -> object:\n"
        '    return literal_column("a{}".format(x))\n',
        "literal_column() receives a string built at runtime",
        id="format-literal-column",
    ),
    pytest.param(
        'async def q(conn: object, x: str) -> None:\n    await conn.exec_driver_sql("SELECT 1")\n',
        "exec_driver_sql is not used",
        id="exec-driver-sql",
    ),
    pytest.param(
        'async def q(conn: object, x: str) -> None:\n    await conn.execute(f"SELECT {x}")\n',
        "execute() receives SQL text",
        id="execute-f-string",
    ),
    pytest.param(
        "from sqlalchemy import literal\n\ndef q(x: str) -> object:\n    return literal(x)\n",
        "literal() takes no runtime data",
        id="literal-runtime",
    ),
    pytest.param(
        "from sqlalchemy import MetaData\n\nasync def q(conn: object) -> None:\n"
        "    MetaData().create_all(conn)\n",
        "create_all() changes or reads the schema at runtime",
        id="create-all",
    ),
    pytest.param(
        "from sqlalchemy.ext.asyncio import create_async_engine\n\n"
        'engine = create_async_engine("postgresql+psycopg://", echo=True)\n',
        "create_async_engine() without hide_parameters=True",
        id="engine-without-hidden-parameters",
    ),
    pytest.param(
        "from typing import LiteralString, cast\n\ndef q(x: str) -> LiteralString:\n"
        "    return cast(LiteralString, x)\n",
        "cast(LiteralString, ...)",
        id="cast-literal-string",
    ),
    pytest.param(
        "async def q(conn: object, stmt: object) -> None:\n"
        "    await conn.execute(stmt)  # type: ignore[arg-type]\n",
        "type: ignore on a execute() line",
        id="type-ignore-on-execute",
    ),
]


@pytest.mark.parametrize(("source", "reason"), PLANTED)
def test_a_planted_violation_is_reported(source: str, reason: str) -> None:
    found = violations(source)
    assert any(reason in v for v in found), found


def test_a_planted_module_on_disk_is_reported(tmp_path: Path) -> None:
    """The same scan over a file, as the package scan reads it."""
    planted = tmp_path / "planted_store.py"
    planted.write_text(
        'from sqlalchemy import text\n\ndef q(x: str) -> object:\n    return text(f"SELECT {x}")\n',
        encoding="utf-8",
    )
    assert violations(planted.read_text(encoding="utf-8"), str(planted)) != []


def test_constant_text_and_core_statements_pass() -> None:
    source = (
        "from typing import Final\n"
        "from sqlalchemy import bindparam, literal, select, text\n"
        'Q: Final = "SELECT 1 WHERE :x = 1"\n\n'
        "async def q(conn: object, x: int) -> None:\n"
        "    await conn.execute(text(Q).bindparams(bindparam('x', x)))\n"
        "    await conn.execute(select(literal(0)))\n"
    )
    assert violations(source) == []
