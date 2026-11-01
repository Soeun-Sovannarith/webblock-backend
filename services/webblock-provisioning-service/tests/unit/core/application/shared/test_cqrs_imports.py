"""TR-54 (decision 0025 CQ7): the CQRS bases are used through their module, ``cqrs.Command``
and ``cqrs.Query``, never imported bare, so they are never mistaken for the domain's ``Command``
(the row of provisioning.commands)."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
CQRS_MODULE = "provisioning.core.application.shared.cqrs"
BASES = frozenset({"Command", "Query"})


def bare_imports(source: str) -> list[str]:
    """The CQRS base names a module imports bare, as ``from <cqrs module> import Command``."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module == CQRS_MODULE:
            found.extend(alias.name for alias in node.names if alias.name in BASES)
    return found


def test_tr54_no_module_imports_a_cqrs_base_bare() -> None:
    offenders = {
        path.relative_to(ROOT).as_posix(): names
        for folder in ("src", "tests", "scripts")
        for path in sorted((ROOT / folder).rglob("*.py"))
        if (names := bare_imports(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}


def test_tr54_the_check_finds_a_bare_import() -> None:
    planted = f"from {CQRS_MODULE} import Command, CommandBus\nfrom {CQRS_MODULE} import Query\n"
    assert bare_imports(planted) == ["Command", "Query"]
    assert bare_imports("from provisioning.core.application.shared import cqrs\n") == []
    assert bare_imports(f"from {CQRS_MODULE} import CommandBus, QueryBus\n") == []
