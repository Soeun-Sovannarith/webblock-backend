"""scripts/py/cover.py, the coverage gates of make py-cover (task H1, finding M-1 of wave 6).

- The statement inventory reads the project's coverage settings, so it counts what the report
  counts (``exclude_lines = []``: an ``if TYPE_CHECKING:`` block and a ``...`` stub are statements).
- The total gate combines the data of separately run suites. A suite's data is accepted only with
  its JUnit result (tests ran, none failed, errored or skipped) and a stamp of the source it
  measured; a missing, failed or stale suite fails the gate, never passes it quietly.
"""

import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest
from coverage import CoverageData

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "py" / "cover.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("py_cover_gate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cover = load()

MODULE = """\
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


class Port:
    def call(self) -> None: ...


def used(values: "Sequence[int]") -> int:
    return sum(values)
"""

JUNIT_OK = (
    '<testsuites><testsuite name="pytest" tests="3" failures="0" errors="0" skipped="0"/>'
    "</testsuites>"
)


def package(tmp_path: Path) -> Path:
    root = tmp_path / "src" / "pkg"
    root.mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "mod.py").write_text(MODULE)
    return root


def write_data(path: Path, lines: dict[str, list[int]]) -> None:
    data = CoverageData(basename=str(path))
    data.add_lines(lines)
    data.write()


def record_suite(data_dir: Path, suite: str, root: Path, junit: str = JUNIT_OK) -> None:
    """What a make target run with PY_COVER=1 leaves behind: stamp, data and JUnit result."""
    cover.stamp(suite, data_dir, root)
    write_data(data_dir / f"{suite}.coverage", {str(root / "mod.py"): [1, 3]})
    (data_dir / f"{suite}.junit.xml").write_text(junit)


def test_the_inventory_counts_type_checking_blocks_and_stubs_as_the_report_does(
    tmp_path: Path,
) -> None:
    root = package(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    # 1 import, the if, its import, the class, the def with its stub body, the def, the return.
    assert cover.count_statements(root, work, cover.PYPROJECT) == 7


def test_the_inventory_counts_only_its_directory_when_the_package_is_imported(
    tmp_path: Path,
) -> None:
    """Found in the full unit run: with provisioning already imported, the settings file's
    source_pkgs = ["provisioning"] added the whole package to an inventory of another folder."""
    import provisioning.core.domain

    assert provisioning.core.domain.__name__ == "provisioning.core.domain"
    root = package(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    assert cover.count_statements(root, work, cover.PYPROJECT) == 7


def test_the_project_settings_exclude_nothing() -> None:
    """The inventory and the report share pyproject.toml; it must keep exclude_lines empty."""
    import tomllib

    settings = tomllib.loads(cover.PYPROJECT.read_text())
    assert settings["tool"]["coverage"]["report"]["exclude_lines"] == []


def test_a_recorded_suite_is_accepted(tmp_path: Path) -> None:
    root = package(tmp_path)
    data_dir = tmp_path / ".coverage-data"
    record_suite(data_dir, "py-test-integration", root)
    data = cover.check_suite(data_dir, "py-test-integration", root, cover.source_digest(root))
    assert data == data_dir / "py-test-integration.coverage"


@pytest.mark.parametrize("missing", ["coverage", "junit.xml", "stamp.json"])
def test_a_suite_with_a_missing_file_fails(tmp_path: Path, missing: str) -> None:
    root = package(tmp_path)
    data_dir = tmp_path / ".coverage-data"
    record_suite(data_dir, "py-test-messaging", root)
    (data_dir / f"py-test-messaging.{missing}").unlink()
    with pytest.raises(cover.GateFailure, match=r"make py-test-messaging PY_COVER=1"):
        cover.check_suite(data_dir, "py-test-messaging", root, cover.source_digest(root))


def test_a_suite_that_never_ran_fails(tmp_path: Path) -> None:
    root = package(tmp_path)
    with pytest.raises(cover.GateFailure, match=r"proto-test-kind: no coverage data"):
        cover.check_suite(tmp_path / "none", "proto-test-kind", root, cover.source_digest(root))


@pytest.mark.parametrize(
    ("junit", "reason"),
    [
        ('<testsuite tests="3" failures="1" errors="0" skipped="0"/>', "1 failed"),
        ('<testsuite tests="3" failures="0" errors="2" skipped="0"/>', "2 errors"),
        ('<testsuite tests="3" failures="0" errors="0" skipped="1"/>', "1 skipped"),
        ('<testsuite tests="0" failures="0" errors="0" skipped="0"/>', "ran no test"),
        ("<testsuite", "unreadable"),
    ],
)
def test_a_suite_that_did_not_pass_cleanly_fails(tmp_path: Path, junit: str, reason: str) -> None:
    root = package(tmp_path)
    data_dir = tmp_path / ".coverage-data"
    record_suite(data_dir, "py-test-jenkins", root, junit)
    with pytest.raises(cover.GateFailure, match=reason):
        cover.check_suite(data_dir, "py-test-jenkins", root, cover.source_digest(root))


def test_data_recorded_against_other_source_fails(tmp_path: Path) -> None:
    root = package(tmp_path)
    data_dir = tmp_path / ".coverage-data"
    record_suite(data_dir, "py-test-integration", root)
    (root / "mod.py").write_text(MODULE + "\nVALUE = 1\n")
    with pytest.raises(cover.GateFailure, match="source has changed"):
        cover.check_suite(data_dir, "py-test-integration", root, cover.source_digest(root))


def test_data_recorded_in_another_checkout_fails(tmp_path: Path) -> None:
    root = package(tmp_path)
    data_dir = tmp_path / ".coverage-data"
    record_suite(data_dir, "py-test-integration", root)
    write_data(data_dir / "py-test-integration.coverage", {"/elsewhere/src/pkg/mod.py": [1]})
    with pytest.raises(cover.GateFailure, match="outside"):
        cover.check_suite(data_dir, "py-test-integration", root, cover.source_digest(root))


def test_a_stamp_for_an_unknown_suite_is_refused(tmp_path: Path) -> None:
    with pytest.raises(cover.GateFailure, match="not a suite"):
        cover.stamp("py-test", tmp_path, package(tmp_path))


def test_a_new_stamp_removes_the_previous_run_of_that_suite_only(tmp_path: Path) -> None:
    root = package(tmp_path)
    data_dir = tmp_path / ".coverage-data"
    record_suite(data_dir, "py-test-integration", root)
    record_suite(data_dir, "py-test-messaging", root)
    cover.stamp("py-test-integration", data_dir, root)
    assert not (data_dir / "py-test-integration.coverage").exists()
    assert not (data_dir / "py-test-integration.junit.xml").exists()
    assert (data_dir / "py-test-messaging.coverage").exists()
    stamp = json.loads((data_dir / "py-test-integration.stamp.json").read_text())
    assert stamp["suite"] == "py-test-integration"
    assert stamp["source_sha256"] == cover.source_digest(root)


def test_combined_suites_add_their_lines(tmp_path: Path) -> None:
    root = package(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    first, second = work / "a.coverage", work / "b.coverage"
    write_data(first, {str(root / "mod.py"): [1, 3]})
    write_data(second, {str(root / "mod.py"): [3, 7, 11]})
    report = cover.combine([first, second], work)
    result = cover.measured_under(report, root)
    assert (result.statements, result.covered) == (7, 4)
    assert first.exists() and second.exists()


# --- the minimums (finding F-3 of H-V) ------------------------------------------------------------
# CLAUDE.md section 4, "Coverage": at least 80% of the statements in each of the domain and
# application packages, and 70% across the handwritten packages. check_core and check_total are
# fed a measurement just below and exactly at each minimum, so lowering a minimum, raising it, or
# forcing a verdict to "passed" fails these tests.

STATEMENTS = 1000


def thousand_statements(tmp_path: Path) -> Path:
    """A package of exactly STATEMENTS statements, one per line of mod.py (lines 1 to 1000)."""
    root = tmp_path / "src" / "pkg"
    root.mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "mod.py").write_text("".join(f"v{n} = {n}\n" for n in range(STATEMENTS)))
    return root


def covering(root: Path, covered: int) -> Callable[[list[Path], str | None, str, Path, str], Path]:
    """A run_pytest stand-in whose data covers the first ``covered`` lines of mod.py."""

    def run_pytest(
        targets: list[Path], cov: str | None, marker: str, workdir: Path, label: str
    ) -> Path:
        data_file = workdir / f".coverage.{label}"
        write_data(data_file, {str(root / "mod.py"): list(range(1, covered + 1))})
        return data_file

    return run_pytest


def core_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, covered: int) -> bool:
    root = thousand_statements(tmp_path)
    mirror = tmp_path / "tests" / "unit" / "pkg"
    mirror.mkdir(parents=True)
    (mirror / "test_mod.py").write_text("def test_nothing() -> None:\n    pass\n")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(cover, "package_dir", lambda package: root)
    monkeypatch.setattr(cover, "mirror_dir", lambda package: mirror)
    monkeypatch.setattr(cover, "run_pytest", covering(root, covered))
    result: bool = cover.check_core("provisioning.core.domain", work)
    return result


def total_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, covered: int) -> bool:
    root = thousand_statements(tmp_path)
    data_dir = tmp_path / ".coverage-data"
    for suite in cover.RECORDED_SUITES:
        record_suite(data_dir, suite, root)  # each recording covers lines 1 and 3
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(cover, "PACKAGE_ROOT", root)
    monkeypatch.setattr(cover, "DATA_DIR", data_dir)
    monkeypatch.setattr(cover, "run_pytest", covering(root, covered))
    result: bool = cover.check_total(work)
    return result


@pytest.mark.parametrize(("covered", "passes"), [(799, False), (800, True)])
def test_a_core_package_needs_80_percent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    covered: int,
    passes: bool,
) -> None:
    """79.9% fails and 80.0% passes (CLAUDE.md section 4)."""
    assert core_gate(tmp_path, monkeypatch, covered) is passes
    line = capsys.readouterr().out.strip().splitlines()[-1]
    assert line.startswith(f"py-cover: provisioning.core.domain: {covered / 10:.1f}% of 1000 ")
    assert line.endswith("(minimum 80%): " + ("passed" if passes else "FAILED"))


@pytest.mark.parametrize(("covered", "passes"), [(699, False), (700, True)])
def test_the_total_needs_70_percent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    covered: int,
    passes: bool,
) -> None:
    """69.9% fails and 70.0% passes (CLAUDE.md section 4), the suites combined."""
    assert total_gate(tmp_path, monkeypatch, covered) is passes
    line = capsys.readouterr().out.strip().splitlines()[-1]
    assert line == (
        f"py-cover: total, provisioning: {covered / 10:.1f}% of 1000 statements "
        "(minimum 70%): " + ("passed" if passes else "FAILED")
    )


@pytest.mark.parametrize(
    ("core", "total", "status"),
    [
        ((True, True), True, 0),
        ((False, True), True, 1),
        ((True, False), True, 1),
        ((True, True), False, 1),
    ],
)
def test_any_failed_gate_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, core: tuple[bool, bool], total: bool, status: int
) -> None:
    verdicts = dict(zip(cover.CORE_PACKAGES, core, strict=True))
    monkeypatch.chdir(cover.ROOT)
    monkeypatch.setattr(cover, "check_core", lambda package, workdir: verdicts[package])
    monkeypatch.setattr(cover, "check_total", lambda workdir: total)
    assert cover.main([]) == status
