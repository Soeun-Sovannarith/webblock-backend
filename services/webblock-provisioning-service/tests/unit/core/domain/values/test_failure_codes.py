"""The failure-code catalogue (task H5a; decision 0015 section 4 step 1, decision 0011 section 3).

The catalogue must name every code that can land in ``releases.failure_code``,
``release_steps.failure_code`` or ``commands.failure_code``, and ``OTHER_CODES`` every code the
service writes anywhere else (the dispatch row, an audit event, the inbox, the usage outbox). The
completeness test scans the package's source, so a code added without a catalogue entry fails here,
and so does an entry that nothing writes.

How the scan finds a written code (``_written_codes``):

- a loaded name matching ``CODE`` as a word (``CODE_X``, ``FAILURE_CODE_X``) that resolves, through
  ``from ... import`` chains, to a module-level string constant;
- the first argument of ``Failure``, ``PermanentFailure`` or ``RetryLater``, and the value of a
  ``failure_code=`` keyword: a string literal (also inside ``or`` and ``if`` expressions) or a
  name resolving to a string constant;
- the codes the build-system adapter reports, its public string constants that it uses, and the
  codes the trusted build template reports (``pending = '<code>'`` in the Jenkinsfile).

Not scanned as writes: the API error codes (``presentation.operations.errors``, decision 0006
section 3), log field names (``infrastructure.telemetry``), the catalogue itself, and the deploy
failure list (``deploy_failures``), which lists codes but writes none.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from provisioning.core.domain import (
    FAILURE_CODES,
    MAX_CODE_LEN,
    OTHER_CODES,
    FailurePlace,
    failure_code_entry,
    failure_retryable,
    validate_code,
)

ROOT = Path(__file__).resolve().parents[5]
SRC = ROOT / "src"
PACKAGE = SRC / "provisioning"
JENKINSFILE = ROOT / "deploy" / "prototype" / "jenkins" / "Jenkinsfile"
RUNBOOK = ROOT / ".claude" / "docs" / "runbooks" / "failure-codes.md"

_CODE_NAME = re.compile(r"(^|_)CODE(_|$)")
_CODE_VALUE = re.compile(r"[a-z][a-z0-9_]{0,99}")
_FAILURE_CALLS = frozenset({"Failure", "PermanentFailure", "RetryLater"})
_FAILURE_KEYWORDS = frozenset({"failure_code"})
# Modules whose constants are not failure codes.
_NOT_FAILURE_CODES = (
    "provisioning.presentation.operations.errors",  # API error codes (decision 0006 section 3)
    "provisioning.infrastructure.telemetry",  # log field names
)
# Modules whose references list codes without writing them.
_LISTINGS = frozenset(
    {
        "provisioning.core.domain.values.failure_codes",
        "provisioning.core.domain.values.deploy_failures",
    }
)
# The build-system adapter: its public string constants are codes it reports.
_BUILD_SYSTEM_ADAPTERS = frozenset({"provisioning.infrastructure.jenkins.build_system"})
_TEMPLATE_CODE = re.compile(r"""pending\s*=\s*'([a-z][a-z0-9_]*)'""")


@dataclass(frozen=True, slots=True)
class _Module:
    tree: ast.Module
    constants: dict[str, str]
    imports: dict[str, tuple[str, str]]  # local name -> (module, name)


def _module_name(path: Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _parse(source: str) -> _Module:
    tree = ast.parse(source)
    constants: dict[str, str] = {}
    imports: dict[str, tuple[str, str]] = {}
    for node in tree.body:
        target: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        value = getattr(node, "value", None)
        if (
            isinstance(target, ast.Name)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            constants[target.id] = value.value
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                imports[alias.asname or alias.name] = (node.module, alias.name)
    return _Module(tree, constants, imports)


def _resolve(modules: dict[str, _Module], module: str, name: str) -> tuple[str, str] | None:
    """(defining module, value) of a string constant, following ``from ... import`` chains."""
    seen: set[tuple[str, str]] = set()
    while (module, name) not in seen and module in modules:
        seen.add((module, name))
        mod = modules[module]
        if name in mod.constants:
            return module, mod.constants[name]
        if name not in mod.imports:
            return None
        module, name = mod.imports[name]
    return None


def _values(node: ast.expr) -> Iterator[ast.expr]:
    """The expressions a value can come from: both sides of ``or``/``and`` and of ``x if c else
    y``."""
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            yield from _values(value)
    elif isinstance(node, ast.IfExp):
        yield from _values(node.body)
        yield from _values(node.orelse)
    else:
        yield node


def _is_failure_code_module(module: str) -> bool:
    return not any(module == m or module.startswith(m + ".") for m in _NOT_FAILURE_CODES)


def _written_codes(modules: dict[str, _Module]) -> dict[str, set[str]]:
    """Each code written somewhere, with the modules that write it."""
    found: dict[str, set[str]] = {}

    def add(module: str, resolved: tuple[str, str] | None) -> None:
        if resolved is not None and _is_failure_code_module(resolved[0]):
            found.setdefault(resolved[1], set()).add(module)

    for name, mod in modules.items():
        if name in _LISTINGS or not _is_failure_code_module(name):
            continue
        for node in ast.walk(mod.tree):
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and _CODE_NAME.search(node.id)
            ):
                add(name, _resolve(modules, name, node.id))
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else ""
            )
            candidates = [k.value for k in node.keywords if k.arg in _FAILURE_KEYWORDS]
            if called in _FAILURE_CALLS and node.args:
                candidates.append(node.args[0])
            for candidate in candidates:
                for value in _values(candidate):
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        found.setdefault(value.value, set()).add(name)
                    elif isinstance(value, ast.Name):
                        add(name, _resolve(modules, name, value.id))
        if name in _BUILD_SYSTEM_ADAPTERS:
            used = {
                n.id
                for n in ast.walk(mod.tree)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            }
            for constant, text in mod.constants.items():
                if (
                    not constant.startswith("_")
                    and constant in used
                    and _CODE_VALUE.fullmatch(text)
                ):
                    found.setdefault(text, set()).add(name)
    return found


def _package_modules() -> dict[str, _Module]:
    return {_module_name(p): _parse(p.read_text()) for p in sorted(PACKAGE.rglob("*.py"))}


def _template_codes() -> set[str]:
    return set(_TEMPLATE_CODE.findall(JENKINSFILE.read_text()))


def _all_written() -> set[str]:
    return set(_written_codes(_package_modules())) | _template_codes()


def _catalogued() -> set[str]:
    return {entry.code for entry in FAILURE_CODES} | {other.code for other in OTHER_CODES}


# --- the scan finds what it claims to find -------------------------------------------------------


def test_the_scan_finds_constants_literals_and_imported_names() -> None:
    modules = {
        "provisioning.a": _parse('CODE_ONE = "one_code"\nPLAIN = "not_a_code"\n'),
        "provisioning.b": _parse(
            "from provisioning.a import CODE_ONE, PLAIN\n"
            "def f(x):\n"
            "    raise PermanentFailure(CODE_ONE)\n"
            "def g(x):\n"
            '    x.finish(failure_code=x.code or "fallback_code")\n'
            "def h():\n"
            '    return Failure("literal_code"), PLAIN\n'
            "def i():\n"
            '    raise RetryLater("retry_code")\n'
            "def j():\n"
            '    raise PermanentFailure("permanent_code")\n'
        ),
        "provisioning.presentation.operations.errors": _parse('CODE_API = "api_code"\n'),
        "provisioning.c": _parse(
            "from provisioning.presentation.operations.errors import CODE_API\nuse(CODE_API)\n"
        ),
    }

    found = _written_codes(modules)

    assert set(found) == {
        "one_code",
        "fallback_code",
        "literal_code",
        "retry_code",
        "permanent_code",
    }
    assert found["one_code"] == {"provisioning.b"}


def test_the_scan_finds_the_build_templates_codes() -> None:
    assert _template_codes() >= {"invalid_parameters", "clone_failed", "push_failed"}


# --- completeness, both directions --------------------------------------------------------------


def test_every_written_code_is_in_the_catalogue() -> None:
    missing = sorted(_all_written() - _catalogued())
    assert missing == [], (
        f"codes written but missing from core/domain/values/failure_codes.py: {missing}"
    )


def test_every_catalogue_entry_is_written_somewhere() -> None:
    unwritten = sorted(_catalogued() - _all_written())
    assert unwritten == [], f"catalogue entries that nothing writes: {unwritten}"


# --- the entries ---------------------------------------------------------------------------------


@pytest.mark.parametrize("code", sorted(_catalogued()))
def test_every_code_is_valid_and_fits_its_column(code: str) -> None:
    validate_code(code)
    assert len(code.encode("utf-8")) <= MAX_CODE_LEN


def test_codes_are_listed_once() -> None:
    codes = [entry.code for entry in FAILURE_CODES] + [other.code for other in OTHER_CODES]
    assert len(codes) == len(set(codes))


def test_every_entry_has_a_place_and_a_meaning() -> None:
    for entry in FAILURE_CODES:
        assert entry.places, entry.code
        assert entry.places <= set(FailurePlace), entry.code
        assert entry.meaning.endswith("."), entry.code
    for other in OTHER_CODES:
        assert other.where and other.meaning.endswith("."), other.code


@pytest.mark.parametrize(
    ("code", "retryable", "places"),
    [
        ("insufficient_resources", True, {FailurePlace.STEP, FailurePlace.COMMAND}),
        ("quota_exceeded", False, {FailurePlace.STEP, FailurePlace.COMMAND}),
        ("dependency_unavailable", True, {FailurePlace.COMMAND, FailurePlace.RELEASE}),
        ("candidate_refused", False, set(FailurePlace)),
        ("deadline_exceeded", True, set(FailurePlace)),
        ("build_attempts_exhausted", False, {FailurePlace.RELEASE}),
    ],
)
def test_entries_of_note(code: str, retryable: bool, places: set[FailurePlace]) -> None:
    entry = failure_code_entry(code)
    assert entry is not None
    assert (entry.retryable, set(entry.places)) == (retryable, places)
    assert failure_retryable(code) is retryable


def test_an_unknown_or_other_code_is_not_retryable() -> None:
    assert failure_code_entry("no_such_code") is None
    assert failure_retryable("no_such_code") is False
    assert failure_code_entry("transient_failure") is None  # a dispatch-row code


# --- the human-readable catalogue ----------------------------------------------------------------


def _table() -> str:
    lines = [
        "| Code | Stage | Recorded on | Retryable | Meaning |",
        "| --- | --- | --- | --- | --- |",
    ]
    order = (FailurePlace.RELEASE, FailurePlace.STEP, FailurePlace.COMMAND)
    for entry in FAILURE_CODES:
        places = ", ".join(p.value for p in order if p in entry.places)
        retry = "yes" if entry.retryable else "no"
        lines.append(
            f"| `{entry.code}` | {entry.stage.value} | {places} | {retry} | {entry.meaning} |"
        )
    lines += ["", "| Code | Written to | Meaning |", "| --- | --- | --- |"]
    lines += [f"| `{o.code}` | {o.where} | {o.meaning} |" for o in OTHER_CODES]
    return "\n".join(lines)


_BEGIN = (
    "<!-- catalogue:begin (generated from core/domain/values/failure_codes.py; "
    "checked by a test) -->"
)
_END = "<!-- catalogue:end -->"


def test_the_runbook_shows_the_catalogue() -> None:
    text = RUNBOOK.read_text()
    assert _BEGIN in text and _END in text, "the runbook lacks the catalogue markers"
    shown = text.split(_BEGIN, 1)[1].split(_END, 1)[0].strip()
    assert shown == _table(), (
        ".claude/docs/runbooks/failure-codes.md differs; expected:\n" + _table()
    )
