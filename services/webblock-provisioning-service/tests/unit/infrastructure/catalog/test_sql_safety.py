"""No SQL built by string formatting in the catalog code (the owner's SQL-safety requirement,
proto-sql-safety.md; decision 0014 section 14).

Every module under ``src/provisioning/infrastructure/catalog/`` and the stand-in tool
``src/provisioning/hosts/devtools/catalog_platform.py`` is parsed with ``ast``. The first argument
of every ``execute(...)`` call, and of every ``psycopg.sql.SQL(...)``, must be a string literal or a
name bound to a module-level string constant: values travel as bound parameters, identifiers only
through ``sql.Identifier`` and the one SCRAM verifier through ``sql.Literal``. An f-string, ``%``,
``+`` or ``.format`` on a string anywhere inside such a call fails the test (``sql.SQL(...).format``
is the safe composition and is allowed). Planted violations show that the check fires.
"""

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
CHECKED = [
    *sorted((REPO / "src/provisioning/infrastructure/catalog").glob("*.py")),
    REPO / "src/provisioning/hosts/devtools/catalog_platform.py",
]
SQL_CALLS = {"execute", "SQL"}


def _constants(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign | ast.AnnAssign):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                names.update(t.id for t in targets if isinstance(t, ast.Name))
    return names


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _is_sql_compose(node: ast.AST) -> bool:
    """``sql.SQL(...).format(...)`` or ``sql.SQL(...).join(...)``: composition of SQL objects."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("format", "join")
        and isinstance(node.func.value, ast.Call)
        and _call_name(node.func.value) == "SQL"
    )


def _string_building(node: ast.AST) -> list[str]:
    found = []
    for inner in ast.walk(node):
        if isinstance(inner, ast.JoinedStr):
            found.append("f-string")
        elif isinstance(inner, ast.BinOp) and isinstance(inner.op, ast.Mod | ast.Add):
            found.append("% or + on a string")
        elif (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr in ("format", "join")
            and not _is_sql_compose(inner)
        ):
            found.append(f".{inner.func.attr} on a string")
    return found


def violations(source: str) -> list[str]:
    """Each ``execute``'s statement is a string literal, a module-level string constant, or
    ``sql.SQL(<literal or constant>)``, optionally composed with ``.format`` or ``.join``; nothing
    inside it builds a string."""
    tree = ast.parse(source)
    constants = _constants(tree)

    def constant(node: ast.AST) -> bool:
        return (isinstance(node, ast.Constant) and isinstance(node.value, str)) or (
            isinstance(node, ast.Name) and node.id in constants
        )

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) not in SQL_CALLS or not node.args:
            continue
        first = node.args[0]
        line = getattr(node, "lineno", 0)
        if _call_name(node) == "SQL":
            if not constant(first):
                found.append(f"line {line}: sql.SQL of something other than a constant")
            continue
        statement: ast.AST = first
        if isinstance(first, ast.Call) and isinstance(first.func, ast.Attribute):
            if _is_sql_compose(first):
                statement = first.func.value
        if not (constant(statement) or _call_name_is_sql(statement)):
            found.append(f"line {line}: execute of something other than constant or composed SQL")
        found.extend(f"line {line}: {kind}" for kind in _string_building(first))
    return found


def _call_name_is_sql(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and _call_name(node) == "SQL"


@pytest.mark.parametrize("path", CHECKED, ids=lambda p: p.name)
def test_the_catalog_code_builds_no_sql_from_strings(path: Path) -> None:
    assert path.exists(), path
    assert violations(path.read_text()) == []


@pytest.mark.parametrize(
    "planted",
    [
        'async def f(c, role):\n    await c.execute(f"CREATE ROLE {role}")\n',
        'async def f(c, role):\n    await c.execute("CREATE ROLE %s" % role)\n',
        'async def f(c, role):\n    await c.execute("CREATE ROLE " + role)\n',
        'async def f(c, role):\n    await c.execute("CREATE ROLE {}".format(role))\n',
        "from psycopg import sql\nasync def f(c, role):\n"
        '    await c.execute(sql.SQL(f"CREATE ROLE {role}"))\n',
        "from psycopg import sql\nasync def f(c, role):\n"
        '    await c.execute(sql.SQL("CREATE ROLE " + role))\n',
        "from psycopg import sql\nasync def f(c, q):\n    await c.execute(sql.SQL(q))\n",
        'async def f(c, role):\n    await c.execute(" ".join(("GRANT", role)))\n',
        'async def f(c, role):\n    q = f"CREATE ROLE {role}"\n    await c.execute(q)\n',
    ],
)
def test_a_planted_violation_is_found(planted: str) -> None:
    assert violations(planted) != []


@pytest.mark.parametrize(
    "safe",
    [
        'Q = "SELECT 1 WHERE x = %s"\nasync def f(c, v):\n    await c.execute(Q, (v,))\n',
        'async def f(c, v):\n    await c.execute("SELECT %s", (v,))\n',
        'from psycopg import sql\nCREATE = "CREATE ROLE {} PASSWORD {}"\n'
        "async def f(c, r, v):\n"
        "    await c.execute(sql.SQL(CREATE).format(sql.Identifier(r), sql.Literal(v)))\n",
    ],
)
def test_safe_statements_pass(safe: str) -> None:
    assert violations(safe) == []
