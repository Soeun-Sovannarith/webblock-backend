"""spec-check: the spec register against the spec, tests, commits, designs and plans.

Run by ``make spec-check`` as ``python3.14 -I scripts/py/spec_check.py`` from the module root.
The register is ``.claude/docs/requirements/spec.yaml``: every BR, PR, FR and TR of BRD.md,
PRD.md, FRD.md and TRD.md with its title, status (current or removed), parents (BR for a PR, PR
for an FR, FR for a TR), priority and phase, and for each FR its acceptance rows and the tests
that carry its ID. It is kept by hand.

Standard library only; spec.yaml is read with the small YAML subset parser below, not PyYAML.
PyYAML is no direct dependency of this repo (only a runtime dependency of kubernetes-asyncio),
and the SessionStart hook reads the register with the system Python and no packages. The
subset: mappings, lists, ``key: value``, double-quoted strings (JSON escapes), flow lists
``[a, b]``, plain scalars, ``true``, ``false``, integers and comment lines. Any other syntax is
an error. The file stays valid YAML, so every YAML tool reads it the same way.

Checks:

- the register: known fields, unique IDs, status current or removed, rows of their own FR;
- parents: every parent exists and has the right kind; no current item points to a removed one;
- documents: the four Markdown documents and the register list the same IDs, statuses and rows;
- citations: every FR-n and TR-n cited in src/ (code), in tests/ (test names and docstrings) and
  in the Markdown under .claude/docs/ (designs, plans and the other docs; not requirements/, which
  defines the IDs, and not a folder's README.md, which only describes) exists and is current.
  Commit messages of ``git log <base>..HEAD`` (base ``main``) are history: an ID cited there must
  exist, but an ID removed after the commit is no problem;
- tests: each FR's ``tests`` list equals the test node IDs that carry the FR's ID (by name
  ``test_fr12_...`` or by a module, class or function docstring naming ``FR-12``);
- Must: every current Must FR has a test or is listed under ``no_test_yet``; an entry there that
  has a test, or is not a current Must FR, is stale.

Exit status 0 after the report: problems are reported, not failed, until the owner makes the
check blocking; with --strict a problem exits 1. Exit 2 when the register cannot be read, or on
a usage error. --self-test runs the checker's own cases and exits 0 when all pass.
"""

import ast
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

REGISTER = Path(".claude/docs/requirements/spec.yaml")
REQUIREMENTS = Path(".claude/docs/requirements")
DOCS = Path(".claude/docs")
CODE = Path("src")
TESTS = Path("tests")
DOC_FILES = {"BR": "BRD.md", "PR": "PRD.md", "FR": "FRD.md", "TR": "TRD.md"}
PARENT_KIND = {"BR": None, "PR": "BR", "FR": "PR", "TR": "FR"}
STATUSES = ("current", "removed")
ID_RE = re.compile(r"^(BR|PR|FR|TR)-([1-9][0-9]*)$")
ROW_RE = re.compile(r"^(FR-[1-9][0-9]*)\.[1-9][0-9]*$")
CITE_RE = re.compile(r"\b([FT]R-[1-9][0-9]*)(?:\.[0-9]+)?\b")
CELL_RE = re.compile(r"(?<!\\)\|")  # a table cell border; "\|" inside a cell is text
NAME_RE = re.compile(r"(?:^|_)(fr|tr)([1-9][0-9]*)(?=_|$)")
FIELDS = {"id", "title", "status", "parents", "priority", "phase"}
EXTRA_FIELDS = {"BR": set(), "PR": set(), "FR": {"rows", "tests"}, "TR": {"all_fr"}}
GIT_SECONDS = 10

type Value = str | int | bool | list[Value] | dict[str, Value] | None


class SpecError(Exception):
    """spec.yaml is not in the YAML subset this script reads."""


# ------------------------------------------------------------------------------------------------
# The YAML subset
# ------------------------------------------------------------------------------------------------

KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(?: (.*))?$")
PLAIN_BAD_START = set("&*!|>'%@{}[],#?:`\"")


@dataclass(frozen=True, slots=True)
class _Line:
    number: int
    indent: int
    text: str


def _logical_lines(text: str) -> list[_Line]:
    """Lines without blanks and comments; ``- key: v`` becomes ``-`` plus ``key: v`` two deeper."""
    out: list[_Line] = []
    for number, raw in enumerate(text.splitlines(), 1):
        body = raw.rstrip()
        stripped = body.lstrip(" ")
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("\t") or "\t" in body[: len(body) - len(stripped)]:
            raise SpecError(f"line {number}: a tab in the indentation")
        indent = len(body) - len(stripped)
        rest = stripped[2:] if stripped.startswith("- ") else None
        if rest is not None and KEY_RE.match(rest):
            out += [_Line(number, indent, "-"), _Line(number, indent + 2, rest)]
        else:
            out.append(_Line(number, indent, stripped))
    return out


def _flow_items(inner: str, number: int) -> list[Value]:
    items: list[Value] = []
    buf, quoted, escaped = "", False, False
    for ch in inner + ",":
        if quoted:
            buf += ch
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
        elif ch == '"':
            buf += ch
            quoted = True
        elif ch == ",":
            if not buf.strip():
                raise SpecError(f"line {number}: an empty item in a flow list")
            items.append(_scalar(buf.strip(), number))
            buf = ""
        elif ch in "[]{}":
            raise SpecError(f"line {number}: a nested flow collection")
        else:
            buf += ch
    if quoted:
        raise SpecError(f"line {number}: an unclosed quote")
    return items


def _scalar(text: str, number: int) -> Value:
    if text.startswith('"'):
        try:
            value = json.loads(text)
        except ValueError as e:
            raise SpecError(f"line {number}: a bad double-quoted string ({e})") from None
        if not isinstance(value, str):
            raise SpecError(f"line {number}: a bad double-quoted string")
        return value
    if text.startswith("["):
        if not text.endswith("]"):
            raise SpecError(f"line {number}: a flow list must end on its line with ]")
        inner = text[1:-1]
        return [] if not inner.strip() else _flow_items(inner, number)
    if text in {"true", "false"}:
        return text == "true"
    if text in {"null", "~"}:
        return None
    if re.fullmatch(r"-?[0-9]+", text):
        return int(text)
    if text[0] in PLAIN_BAD_START or ": " in text or " #" in text or text.endswith(":"):
        raise SpecError(f"line {number}: quote this value: {text[:40]!r}")
    return text


class _Parser:
    def __init__(self, lines: list[_Line]) -> None:
        self.lines = lines
        self.i = 0

    def block(self, indent: int) -> Value:
        line = self.lines[self.i]
        if line.text == "-" or line.text.startswith("- "):
            return self.sequence(indent)
        return self.mapping(indent)

    def sequence(self, indent: int) -> list[Value]:
        items: list[Value] = []
        while self.i < len(self.lines) and self.lines[self.i].indent == indent:
            line = self.lines[self.i]
            if line.text == "-":
                self.i += 1
                if self.i >= len(self.lines) or self.lines[self.i].indent <= indent:
                    raise SpecError(f"line {line.number}: a list item with no value")
                items.append(self.block(self.lines[self.i].indent))
            elif line.text.startswith("- "):
                items.append(_scalar(line.text[2:].strip(), line.number))
                self.i += 1
            else:
                raise SpecError(f"line {line.number}: a mapping key where a list item belongs")
        self._no_deeper(indent)
        return items

    def mapping(self, indent: int) -> dict[str, Value]:
        result: dict[str, Value] = {}
        while self.i < len(self.lines) and self.lines[self.i].indent == indent:
            line = self.lines[self.i]
            match = KEY_RE.match(line.text)
            if not match:
                raise SpecError(f"line {line.number}: expected 'key: value'")
            key, rest = match.group(1), (match.group(2) or "").strip()
            if key in result:
                raise SpecError(f"line {line.number}: the key {key!r} twice")
            self.i += 1
            if rest:
                result[key] = _scalar(rest, line.number)
            elif self.i < len(self.lines) and self.lines[self.i].indent > indent:
                result[key] = self.block(self.lines[self.i].indent)
            else:
                raise SpecError(f"line {line.number}: the key {key!r} has no value")
        self._no_deeper(indent)
        return result

    def _no_deeper(self, indent: int) -> None:
        if self.i < len(self.lines) and self.lines[self.i].indent > indent:
            raise SpecError(f"line {self.lines[self.i].number}: unexpected indentation")


def parse_yaml(text: str) -> Value:
    """Parse the YAML subset described in the module docstring."""
    lines = _logical_lines(text)
    if not lines:
        raise SpecError("the file is empty")
    if lines[0].indent != 0:
        raise SpecError(f"line {lines[0].number}: the first line must not be indented")
    parser = _Parser(lines)
    value = parser.block(0)
    if parser.i != len(lines):
        raise SpecError(f"line {lines[parser.i].number}: unexpected text after the document")
    return value


# ------------------------------------------------------------------------------------------------
# The register
# ------------------------------------------------------------------------------------------------


@dataclass(slots=True)
class Item:
    id: str
    title: str
    status: str
    parents: list[str]
    all_fr: bool = False
    priority: str | None = None
    phase: str | None = None
    rows: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return self.id[:2]

    @property
    def current(self) -> bool:
        return self.status == "current"


@dataclass(slots=True)
class Register:
    version: int
    items: dict[str, Item]
    no_test_yet: list[str]
    problems: list[tuple[str, str]]


def _str_list(value: Value, where: str, problems: list[tuple[str, str]]) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        problems.append(("register", f"{where} must be a list of strings"))
        return []
    return [v for v in value if isinstance(v, str)]


def _item(entry: Value, index: int, problems: list[tuple[str, str]]) -> Item | None:
    if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
        problems.append(("register", f"items[{index}] has no id"))
        return None
    ident = str(entry["id"])
    if not ID_RE.match(ident):
        problems.append(("register", f"{ident}: not a BR-n, PR-n, FR-n or TR-n ID"))
        return None
    kind = ident[:2]
    for key in sorted(set(entry) - FIELDS - EXTRA_FIELDS[kind]):
        problems.append(("register", f"{ident}: unknown field {key!r}"))
    for key in sorted({"id", "title", "status", "parents"} | EXTRA_FIELDS[kind] - {"all_fr"}):
        if key not in entry:
            problems.append(("register", f"{ident}: missing field {key!r}"))
    title, status = entry.get("title"), entry.get("status")
    if not isinstance(title, str) or not title.strip():
        problems.append(("register", f"{ident}: the title must be a non-empty string"))
    if status not in STATUSES:
        problems.append(("register", f"{ident}: status must be current or removed, not {status!r}"))
    priority, phase, all_fr = entry.get("priority"), entry.get("phase"), entry.get("all_fr", False)
    for key, value in (("priority", priority), ("phase", phase)):
        if value is not None and not isinstance(value, str):
            problems.append(("register", f"{ident}: {key} must be a string"))
    if not isinstance(all_fr, bool):
        problems.append(("register", f"{ident}: all_fr must be true or false"))
    item = Item(
        id=ident,
        title=title if isinstance(title, str) else "",
        status=status if isinstance(status, str) else "",
        parents=_str_list(entry.get("parents", []), f"{ident}: parents", problems),
        all_fr=all_fr is True,
        priority=priority if isinstance(priority, str) else None,
        phase=phase if isinstance(phase, str) else None,
        rows=_str_list(entry.get("rows", []), f"{ident}: rows", problems),
        tests=_str_list(entry.get("tests", []), f"{ident}: tests", problems),
    )
    for row in item.rows:
        match = ROW_RE.match(row)
        if not match or match.group(1) != ident:
            problems.append(("register", f"{ident}: row {row} does not belong to it"))
    return item


def load_register(text: str) -> Register:
    """The register from spec.yaml text; SpecError when the text is not in the subset."""
    data = parse_yaml(text)
    if not isinstance(data, dict):
        raise SpecError("the top level must be a mapping")
    problems: list[tuple[str, str]] = []
    for key in sorted(set(data) - {"version", "items", "no_test_yet"}):
        problems.append(("register", f"unknown top-level key {key!r}"))
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        problems.append(("register", "version must be an integer"))
        version = 0
    entries = data.get("items")
    if not isinstance(entries, list):
        raise SpecError("items must be a list")
    items: dict[str, Item] = {}
    for index, entry in enumerate(entries):
        item = _item(entry, index, problems)
        if item is None:
            continue
        if item.id in items:
            problems.append(("register", f"{item.id}: the ID is listed twice"))
            continue
        items[item.id] = item
    no_test_yet = _str_list(data.get("no_test_yet", []), "no_test_yet", problems)
    return Register(version, items, no_test_yet, problems)


# ------------------------------------------------------------------------------------------------
# Sources: the documents, the tests, the commits, the designs and plans
# ------------------------------------------------------------------------------------------------


def document_ids(requirements: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Each ID in the first cell of a table row of its own document, with its status (removed when
    a cell starts with "Removed:"), and the FRD's acceptance rows per FR."""
    statuses: dict[str, str] = {}
    rows: dict[str, list[str]] = {}
    for kind, name in DOC_FILES.items():
        path = requirements / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in CELL_RE.split(line.strip().strip("|"))]
            first = cells[0]
            row = ROW_RE.match(first)
            if kind == "FR" and row:
                rows.setdefault(row.group(1), []).append(first)
            elif first.startswith(kind + "-") and ID_RE.match(first):
                removed = any(c.startswith("Removed:") for c in cells[1:])
                statuses[first] = "removed" if removed else "current"
    return statuses, rows


def _ids_in_text(text: str) -> set[str]:
    return {m.group(1) for m in CITE_RE.finditer(text)}


def _ids_in_name(name: str) -> set[str]:
    return {f"{m.group(1).upper()}-{m.group(2)}" for m in NAME_RE.finditer(name.lower())}


def _docstring_ids(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
        return _ids_in_text(ast.get_docstring(node) or "")
    return set()


def scan_tests(root: Path, tests: Path = TESTS) -> dict[str, set[str]]:
    """Each test node ID under ``tests`` with the FR and TR IDs it carries: its name, its own
    docstring and the docstrings of its class and module."""
    found: dict[str, set[str]] = {}
    for path in sorted((root / tests).rglob("test_*.py")):
        try:
            module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError, UnicodeDecodeError:
            continue
        rel = path.relative_to(root).as_posix()
        base = _docstring_ids(module)
        for node in module.body:
            if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                inherited = base | _docstring_ids(node)
                for sub in node.body:
                    if isinstance(
                        sub, ast.FunctionDef | ast.AsyncFunctionDef
                    ) and sub.name.startswith("test"):
                        ids = inherited | _docstring_ids(sub) | _ids_in_name(sub.name)
                        found[f"{rel}::{node.name}::{sub.name}"] = ids
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
                "test"
            ):
                found[f"{rel}::{node.name}"] = base | _docstring_ids(node) | _ids_in_name(node.name)
    return found


def commit_ids(root: Path, base: str) -> dict[str, set[str]] | None:
    """FR and TR IDs per commit of ``git log <base>..HEAD``; None when git cannot answer."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, never through a shell
            ["git", "-C", str(root), "log", "--format=%h%x00%B%x1e", f"{base}..HEAD", "--"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=GIT_SECONDS,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return None
    if result.returncode != 0:
        return None
    out: dict[str, set[str]] = {}
    for record in result.stdout.split("\x1e"):
        sha, _, body = record.strip("\n").partition("\x00")
        if sha:
            out[sha] = _ids_in_text(body)
    return out


def markdown_ids(root: Path, docs: Path = DOCS) -> dict[str, set[str]]:
    """FR and TR IDs per Markdown file under ``docs``, designs and plans included. The
    requirements folder defines the IDs and each folder's README.md only describes: neither
    cites."""
    out: dict[str, set[str]] = {}
    for path in sorted((root / docs).rglob("*.md")):
        rel = path.relative_to(root / docs)
        if path.name != "README.md" and rel.parts[0] != REQUIREMENTS.name:
            out[path.relative_to(root).as_posix()] = _ids_in_text(path.read_text("utf-8"))
    return out


def code_ids(root: Path, code: Path = CODE) -> dict[str, set[str]]:
    """FR and TR IDs per text file under ``code``; caches and files that are not UTF-8 text are
    skipped."""
    out: dict[str, set[str]] = {}
    for path in sorted((root / code).rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        out[path.relative_to(root).as_posix()] = _ids_in_text(text)
    return out


# ------------------------------------------------------------------------------------------------
# The checks
# ------------------------------------------------------------------------------------------------


@dataclass(slots=True)
class Sources:
    documents: tuple[dict[str, str], dict[str, list[str]]]
    tests: dict[str, set[str]]
    commits: dict[str, set[str]] | None
    markdown: dict[str, set[str]]
    code: dict[str, set[str]] = field(default_factory=dict)


def check_parents(reg: Register) -> list[tuple[str, str]]:
    problems: list[tuple[str, str]] = []
    for item in reg.items.values():
        want = PARENT_KIND[item.kind]
        if item.current and want and not item.parents and not item.all_fr:
            problems.append(("parents", f"{item.id}: no parent {want}"))
        if item.all_fr and item.kind != "TR":
            problems.append(("parents", f"{item.id}: only a TR may serve all FRs"))
        for parent in item.parents:
            target = reg.items.get(parent)
            if target is None:
                problems.append(("parents", f"{item.id}: parent {parent} is not in the register"))
            elif target.kind != want:
                problems.append(("parents", f"{item.id}: parent {parent} is not a {want}"))
            elif item.current and not target.current:
                problems.append(
                    ("parents", f"{item.id} is current but its parent {parent} is removed")
                )
    return problems


def check_documents(reg: Register, src: Sources) -> list[tuple[str, str]]:
    statuses, rows = src.documents
    problems: list[tuple[str, str]] = []
    for ident, status in statuses.items():
        item = reg.items.get(ident)
        if item is None:
            problems.append(("docs", f"{ident} is in {DOC_FILES[ident[:2]]} but not in spec.yaml"))
        elif item.status != status:
            problems.append(
                (
                    "docs",
                    f"{ident} is {status} in {DOC_FILES[ident[:2]]}, {item.status} in spec.yaml",
                )
            )
    for ident in reg.items:
        if ident not in statuses:
            problems.append(("docs", f"{ident} is in spec.yaml but not in {DOC_FILES[ident[:2]]}"))
    for ident, item in reg.items.items():
        if item.kind == "FR" and set(item.rows) != set(rows.get(ident, [])):
            only_doc = sorted(set(rows.get(ident, [])) - set(item.rows))
            only_reg = sorted(set(item.rows) - set(rows.get(ident, [])))
            problems.append(
                ("docs", f"{ident}: rows differ (FRD only {only_doc}, spec.yaml only {only_reg})")
            )
    return problems


def _cited(
    reg: Register, where: str, ids: Iterable[str], *, history: bool = False
) -> list[tuple[str, str]]:
    """An ID that is not in the register; and, unless ``where`` is history, a removed one."""
    problems: list[tuple[str, str]] = []
    for ident in sorted(ids):
        item = reg.items.get(ident)
        if item is None:
            problems.append(("cites", f"{where} cites {ident}, which is not in spec.yaml"))
        elif not item.current and not history:
            problems.append(("cites", f"{where} cites {ident}, which is removed"))
    return problems


def check_citations(reg: Register, src: Sources) -> list[tuple[str, str]]:
    problems: list[tuple[str, str]] = []
    for path, ids in src.code.items():
        problems += _cited(reg, path, ids)
    for node, ids in src.tests.items():
        problems += _cited(reg, node, ids)
    for path, ids in src.markdown.items():
        problems += _cited(reg, path, ids)
    # A commit message is history: it was true when written, and history is never rewritten.
    for sha, ids in (src.commits or {}).items():
        problems += _cited(reg, f"commit {sha}", ids, history=True)
    return problems


def tests_by_fr(tests: dict[str, set[str]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for node, ids in sorted(tests.items()):
        for ident in ids:
            if ident.startswith("FR-"):
                out.setdefault(ident, []).append(node)
    return out


def check_tests(reg: Register, src: Sources) -> list[tuple[str, str]]:
    found = tests_by_fr(src.tests)
    problems: list[tuple[str, str]] = []
    for item in reg.items.values():
        if item.kind != "FR":
            continue
        have = set(found.get(item.id, []))
        for node in sorted(have - set(item.tests)):
            problems.append(("tests", f"{item.id}: {node} carries the ID but is not listed"))
        for node in sorted(set(item.tests) - have):
            problems.append(
                ("tests", f"{item.id}: {node} is listed but no such test carries the ID")
            )
    return problems


def must_frs(reg: Register) -> list[Item]:
    return [i for i in reg.items.values() if i.kind == "FR" and i.current and i.priority == "Must"]


def check_must(reg: Register) -> list[tuple[str, str]]:
    problems: list[tuple[str, str]] = []
    waived = set(reg.no_test_yet)
    for item in must_frs(reg):
        if not item.tests and item.id not in waived:
            problems.append(
                ("must", f"{item.id} is a Must FR with no test and not under no_test_yet")
            )
    musts = {i.id for i in must_frs(reg)}
    for ident in reg.no_test_yet:
        if ident not in musts:
            problems.append(("must", f"no_test_yet lists {ident}, which is not a current Must FR"))
        elif reg.items[ident].tests:
            problems.append(("must", f"no_test_yet lists {ident}, which has a test now"))
    return problems


def run_checks(reg: Register, src: Sources) -> list[tuple[str, str]]:
    return (
        reg.problems
        + check_parents(reg)
        + check_documents(reg, src)
        + check_citations(reg, src)
        + check_tests(reg, src)
        + check_must(reg)
    )


def summary(reg: Register, src: Sources) -> list[str]:
    lines = []
    for kind in ("BR", "PR", "FR", "TR"):
        items = [i for i in reg.items.values() if i.kind == kind]
        current = sum(i.current for i in items)
        extra = f"; acceptance rows {sum(len(i.rows) for i in items)}" if kind == "FR" else ""
        lines.append(
            f"  {kind}: {len(items)} ({current} current, {len(items) - current} removed){extra}"
        )
    frs = [i for i in reg.items.values() if i.kind == "FR" and i.current]
    musts = must_frs(reg)
    with_test = sum(bool(i.tests) for i in musts)
    waived = sum(i.id in reg.no_test_yet for i in musts)
    lines.append(
        f"  Current FRs with a test: {sum(bool(i.tests) for i in frs)} of {len(frs)}; "
        f"Must FRs {len(musts)}: {with_test} with a test, {waived} listed as no test yet"
    )
    commits = "not read (git log failed)" if src.commits is None else f"{len(src.commits)} commits"
    lines.append(
        f"  Read: {len(src.code)} code files, {len(src.tests)} tests, {commits}, "
        f"{len(src.markdown)} docs (designs and plans included)"
    )
    return lines


def gather(root: Path, base: str) -> Sources:
    return Sources(
        documents=document_ids(root / REQUIREMENTS),
        tests=scan_tests(root),
        commits=commit_ids(root, base),
        markdown=markdown_ids(root),
        code=code_ids(root),
    )


def main(argv: list[str]) -> int:
    args = list(argv)
    if "--self-test" in args:
        return self_test()
    strict = "--strict" in args
    args = [a for a in args if a != "--strict"]
    base = "main"
    if args[:1] == ["--base"] and len(args) == 2:
        base = args[1]
    elif args:
        print("usage: spec_check.py [--strict] [--base <ref>] | --self-test", file=sys.stderr)
        return 2
    root = Path.cwd()
    try:
        reg = load_register((root / REGISTER).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SpecError) as e:
        print(f"spec-check: cannot read {REGISTER}: {e}", file=sys.stderr)
        return 2
    src = gather(root, base)
    problems = run_checks(reg, src)
    print(f"spec-check: {REGISTER} (version {reg.version}), commits {base}..HEAD")
    print("\n".join(summary(reg, src)))
    by_kind: dict[str, int] = {}
    for kind, _ in problems:
        by_kind[kind] = by_kind.get(kind, 0) + 1
    counts = ", ".join(f"{k} {n}" for k, n in sorted(by_kind.items())) or "none"
    print(f"Problems: {len(problems)} ({counts})")
    for kind, text in problems:
        print(f"  [{kind}] {text}")
    if problems and strict:
        return 1
    mode = "report only; --strict fails on them" if problems else "clean"
    print(f"spec-check: {len(problems)} problems ({mode})")
    return 0


# ------------------------------------------------------------------------------------------------
# Self-test (the checker's own cases; tests/ gets them as a unit test file later)
# ------------------------------------------------------------------------------------------------

_GOOD = """\
# a comment
version: 1
items:
  - id: BR-1
    title: "Goal: live sites"
    status: current
    parents: []
  - id: PR-1
    title: "Built by itself"
    status: current
    parents: [BR-1]
    priority: Must
    phase: Prototype
  - id: PR-2
    title: "Removed offer"
    status: removed
    parents: [BR-1]
  - id: FR-1
    title: "Build it"
    status: current
    parents: [PR-1]
    priority: Must
    phase: Prototype
    rows: [FR-1.1, FR-1.2]
    tests:
      - tests/unit/test_a.py::test_fr1_builds
  - id: FR-2
    title: "Removed behaviour"
    status: removed
    parents: [PR-2]
    rows: []
    tests: []
  - id: FR-3
    title: "Check it"
    status: current
    parents: [PR-1]
    priority: Must
    phase: Prototype
    rows: [FR-3.1]
    tests: []
  - id: TR-1
    title: "One package"
    status: current
    parents: []
    all_fr: true
no_test_yet:
  - FR-3
"""

_DOCS = {
    "BRD.md": "| BR-1 | Goal | x |\n",
    "PRD.md": "| PR-1 | Offer | BR-1 |\n| PR-2 | Removed: gone; see PR-1 | BR-1 |\n",
    "FRD.md": "| FR-1 | x | PR-1 |\n| FR-2 | Removed: gone | PR-2 |\n| FR-3 | y | PR-1 |\n"
    "| FR-1.1 | g | w | t |\n| FR-1.2 | g | w | t |\n| FR-3.1 | g | w | t |\n",
    "TRD.md": "| TR-1 | One package | All FR |\n",
}

_TEST_FILE = '''"""Module docstring with no ID."""


def test_fr1_builds():
    assert True


class TestThing:
    """TR-1: one package."""

    def test_layout(self):
        assert True
'''


def _with_tree(
    reg_text: str,
    docs: dict[str, str],
    test_file: str,
    design: str = "",
    *,
    files: dict[str, str] | None = None,
    commits: dict[str, set[str]] | None = None,
) -> list[str]:
    """The problems of a tree with ``docs`` as the requirements, ``test_file`` as the one test
    module, ``design`` as one design, ``files`` (path to text) anywhere else, and ``commits`` as
    the commit messages' IDs."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / REQUIREMENTS).mkdir(parents=True)
        for name, text in docs.items():
            (root / REQUIREMENTS / name).write_text(text, encoding="utf-8")
        (root / TESTS / "unit").mkdir(parents=True)
        (root / TESTS / "unit" / "test_a.py").write_text(test_file, encoding="utf-8")
        (root / DOCS / "designs").mkdir(parents=True)
        (root / DOCS / "designs" / "x-design.md").write_text(design, encoding="utf-8")
        for relative, text in (files or {}).items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            # surrogateescape: "\udcff" is the byte 0xff, a file that is not UTF-8 text.
            (root / relative).write_text(text, encoding="utf-8", errors="surrogateescape")
        src = Sources(
            document_ids(root / REQUIREMENTS),
            scan_tests(root),
            commits or {},
            markdown_ids(root),
            code_ids(root),
        )
        return [text for _, text in run_checks(load_register(reg_text), src)]


def _raises(text: str) -> bool:
    try:
        parse_yaml(text)
    except SpecError:
        return True
    return False


def self_test() -> int:
    cases: list[tuple[str, bool]] = []
    clean = _with_tree(_GOOD, _DOCS, _TEST_FILE)
    cases.append(("a consistent tree has no problem", clean == []))
    parsed = parse_yaml('a: "x: y"\nb: [c, "d, e"]\nc:\n  - 1\n  - true\n')
    cases.append(
        (
            "the subset parses quoted strings, flow lists, block lists",
            parsed == {"a": "x: y", "b": ["c", "d, e"], "c": [1, True]},
        )
    )
    cases.append(("a duplicate key is an error", _raises("a: 1\na: 2\n")))
    cases.append(("a plain value with ': ' must be quoted", _raises("a: b: c\n")))
    cases.append(("an anchor is outside the subset", _raises("a: &x 1\n")))
    cases.append(("a nested flow list is an error", _raises("a: [[b]]\n")))
    cases.append(("bad indentation is an error", _raises("a: 1\n  b: 2\n")))
    dup = _GOOD.replace(
        "  - id: TR-1\n",
        '  - id: BR-1\n    title: "x"\n    status: current\n    parents: []\n  - id: TR-1\n',
    )
    cases.append(
        ("a duplicate ID", any("listed twice" in p for p in _with_tree(dup, _DOCS, _TEST_FILE)))
    )
    orphan = _GOOD.replace(
        "parents: [PR-1]\n    priority: Must\n    phase: Prototype\n    rows: [FR-3.1]",
        "parents: [PR-9]\n    priority: Must\n    phase: Prototype\n    rows: [FR-3.1]",
    )
    cases.append(
        (
            "a parent that does not exist",
            any("PR-9 is not in the register" in p for p in _with_tree(orphan, _DOCS, _TEST_FILE)),
        )
    )
    to_removed = _GOOD.replace(
        "parents: [PR-1]\n    priority: Must\n    phase: Prototype\n    rows: [FR-3.1]",
        "parents: [PR-2]\n    priority: Must\n    phase: Prototype\n    rows: [FR-3.1]",
    )
    cases.append(
        (
            "a current item pointing to a removed one",
            any("parent PR-2 is removed" in p for p in _with_tree(to_removed, _DOCS, _TEST_FILE)),
        )
    )
    wrong_kind = _GOOD.replace(
        "parents: [BR-1]\n    priority: Must", "parents: [PR-2]\n    priority: Must"
    )
    cases.append(
        (
            "a parent of the wrong kind",
            any("is not a BR" in p for p in _with_tree(wrong_kind, _DOCS, _TEST_FILE)),
        )
    )
    docs = {**_DOCS, "TRD.md": _DOCS["TRD.md"] + "| TR-2 | New | FR-1 |\n"}
    cases.append(
        (
            "an ID in a document but not in spec.yaml",
            any(
                "TR-2 is in TRD.md but not in spec.yaml" in p
                for p in _with_tree(_GOOD, docs, _TEST_FILE)
            ),
        )
    )
    docs = {**_DOCS, "FRD.md": _DOCS["FRD.md"].replace("| FR-3 | y |", "| FR-3 | Removed: y |")}
    cases.append(
        (
            "a status that differs from the document",
            any("FR-3 is removed in FRD.md" in p for p in _with_tree(_GOOD, docs, _TEST_FILE)),
        )
    )
    docs = {**_DOCS, "FRD.md": _DOCS["FRD.md"] + "| FR-3.2 | g | w | t |\n"}
    cases.append(
        (
            "acceptance rows that differ",
            any("FR-3: rows differ" in p for p in _with_tree(_GOOD, docs, _TEST_FILE)),
        )
    )
    cited = _TEST_FILE + '\n\ndef test_other():\n    """FR-2.1: removed behaviour."""\n'
    found = _with_tree(_GOOD, _DOCS, cited)
    cases.append(
        ("a test citing a removed FR", any("cites FR-2, which is removed" in p for p in found))
    )
    found = _with_tree(_GOOD, _DOCS, _TEST_FILE, design="Covers FR-77 and TR-1.\n")
    cases.append(
        (
            "a design citing an unknown FR",
            any("cites FR-77, which is not in spec.yaml" in p for p in found),
        )
    )
    unlisted = _TEST_FILE + "\n\ndef test_fr3_checks():\n    assert True\n"
    found = _with_tree(_GOOD, _DOCS, unlisted)
    cases.append(
        (
            "a test carrying an FR ID but not listed",
            any("test_fr3_checks carries the ID" in p for p in found),
        )
    )
    cases.append(
        (
            "a stale no_test_yet entry",
            any(
                "FR-3, which has a test now" in p
                for p in _with_tree(
                    _GOOD.replace(
                        "    tests: []\n  - id: TR-1",
                        "    tests:\n      - tests/unit/test_a.py::test_fr3_checks\n  - id: TR-1",
                    ),
                    _DOCS,
                    unlisted,
                )
            ),
        )
    )
    gone = _GOOD.replace(
        "      - tests/unit/test_a.py::test_fr1_builds\n",
        "      - tests/unit/test_a.py::test_gone\n",
    )
    found = _with_tree(gone, _DOCS, _TEST_FILE)
    cases.append(
        ("a listed test that does not exist", any("test_gone is listed but" in p for p in found))
    )
    cases.append(
        (
            "a Must FR with no test and no waiver",
            any(
                "FR-3 is a Must FR with no test" in p
                for p in _with_tree(
                    _GOOD.replace("no_test_yet:\n  - FR-3\n", ""), _DOCS, _TEST_FILE
                )
            ),
        )
    )
    cases.append(
        (
            "an unknown field",
            any(
                "unknown field 'owner'" in p
                for p in _with_tree(
                    _GOOD.replace("    all_fr: true\n", "    all_fr: true\n    owner: x\n"),
                    _DOCS,
                    _TEST_FILE,
                )
            ),
        )
    )
    cases.append(
        (
            "a commit message is history: a removed ID there is no problem",
            _with_tree(_GOOD, _DOCS, _TEST_FILE, commits={"abc1234": {"FR-2", "TR-1"}}) == [],
        )
    )
    found = _with_tree(_GOOD, _DOCS, _TEST_FILE, commits={"abc1234": {"FR-77"}})
    cases.append(
        (
            "a commit citing an ID that was never in the register",
            found == ["commit abc1234 cites FR-77, which is not in spec.yaml"],
        )
    )
    found = _with_tree(_GOOD, _DOCS, _TEST_FILE, files={"src/pkg/mod.py": '"""FR-2.1: old."""\n'})
    cases.append(
        ("code citing a removed FR", found == ["src/pkg/mod.py cites FR-2, which is removed"])
    )
    found = _with_tree(
        _GOOD,
        _DOCS,
        _TEST_FILE,
        files={"src/pkg/__pycache__/mod.pyc": "FR-2", "src/b.bin": "\udcff"},
    )
    cases.append(("caches and binary files are not code", found == []))
    found = _with_tree(
        _GOOD,
        _DOCS,
        _TEST_FILE,
        files={
            ".claude/docs/decisions/0001-x.md": "Covers FR-2.\n",
            ".claude/docs/plans/x-plan.md": "Task 1: FR-1, FR-2.\n",
        },
    )
    cases.append(
        (
            "a doc and a plan citing a removed FR",
            found
            == [
                ".claude/docs/decisions/0001-x.md cites FR-2, which is removed",
                ".claude/docs/plans/x-plan.md cites FR-2, which is removed",
            ],
        )
    )
    found = _with_tree(
        _GOOD, _DOCS, _TEST_FILE, files={".claude/docs/decisions/README.md": "e.g. FR-2\n"}
    )
    cases.append(("a README only describes", found == []))
    cases.append(
        (
            "the requirements define the IDs and cite nothing",
            "FR-2" in _DOCS["FRD.md"] and _with_tree(_GOOD, _DOCS, _TEST_FILE) == [],
        )
    )
    cases.append(
        (
            "a class docstring ID reaches its tests",
            "TR-1"
            in scan_tests_text(_TEST_FILE).get(
                "tests/unit/test_a.py::TestThing::test_layout", set()
            ),
        )
    )
    failed = 0
    for label, ok in cases:
        failed += not ok
        print(f"{'ok ' if ok else 'BAD'} {label}")
    print(f"\n{len(cases) - failed} of {len(cases)} passed")
    return 1 if failed else 0


def scan_tests_text(text: str) -> dict[str, set[str]]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / TESTS / "unit").mkdir(parents=True)
        (root / TESTS / "unit" / "test_a.py").write_text(text, encoding="utf-8")
        return scan_tests(root)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
