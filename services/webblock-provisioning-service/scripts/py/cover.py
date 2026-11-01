"""py-cover: the coverage gates of CLAUDE.md section 4 (decision 0013 ruling 10.10; task H1).

Run by ``make py-cover`` as ``.venv/bin/python -I scripts/py/cover.py`` from the module root.

1. Core gates. Each core package (provisioning.core.domain, provisioning.core.application) needs at
   least 80% statement coverage, credited only from its mirror folder under tests/unit
   (tests/unit/core/domain, tests/unit/core/application). py-cover runs each mirror folder itself.
   - A package with no statements is reported as "empty (no statements yet)": neither passed nor
     failed.
   - A package with statements and no mirror folder, or a mirror folder without tests, fails.
2. Total gate. At least 70% of the whole package across the suites in TOTAL_SUITES, combined
   (``coverage combine``) from separately run suites, so that no single run needs the Kind stack and
   the Testcontainers suites at the same time (finding M-1 of wave 6):
   - "unit": tests/unit and tests/contracts, run by py-cover itself.
   - every suite in RECORDED_SUITES: recorded earlier by its own make target run with
     ``PY_COVER=1`` (mk/python.mk, py_cover_env), which calls ``cover.py stamp <suite>`` and then
     leaves three files in .coverage-data/: ``<suite>.coverage`` (the data),
     ``<suite>.junit.xml`` (pytest's result) and ``<suite>.stamp.json`` (a digest of the source it
     measured).
   A recorded suite counts only when all three files exist, its JUnit result shows tests that ran
   with no failure, error or skip, its stamp matches the current source, and its data names only
   files of this checkout. Anything else fails the gate; a suite is never skipped.
   Not included: tests/e2e (the service runs there as separate processes started by
   scripts/prototype/run-provisioning.sh, outside any coverage measurement).

Both gates compare the report's statement count with an inventory of every .py file in the
package, made with the same settings (pyproject.toml [tool.coverage], nothing excluded). Exit
status 0 when nothing failed, 1 when a gate failed, 2 on a usage or tool error.

    cover.py                  run the gates
    cover.py stamp SUITE      start a recording of SUITE: remove its previous files, write its stamp
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

import coverage
from coverage import CoverageData
from coverage.exceptions import CoverageException, CoverageWarning, NoDataError

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
PYPROJECT = ROOT / "pyproject.toml"
PACKAGE_ROOT = SRC / "provisioning"
DATA_DIR = ROOT / ".coverage-data"
CORE_PACKAGES = ("provisioning.core.domain", "provisioning.core.application")
CORE_MINIMUM = 80.0
TOTAL_MINIMUM = 70.0
RUN_TIMEOUT_SECONDS = 1800

# The suites whose recorded data the total combines, by the make target that records each.
RECORDED_SUITES = (
    "py-test-integration",  # real PostgreSQL 18 (Testcontainers)
    "py-test-messaging",  # real RabbitMQ (Testcontainers)
    "proto-test-kind",  # the Kind stack: tests/integration/{kubernetes,policy,stack}
    "py-test-catalog-live",  # the Kind stack's catalog database: tests/integration/catalog
    "py-test-jenkins",  # the Kind stack's Jenkins: tests/integration/jenkins
)
TOTAL_SUITES = ("unit", *RECORDED_SUITES)


class GateFailure(Exception):
    pass


@dataclass(frozen=True)
class Measured:
    statements: int
    covered: int

    @property
    def percent(self) -> float:
        return 100.0 * self.covered / self.statements


def package_dir(package: str) -> Path:
    return SRC.joinpath(*package.split("."))


def mirror_dir(package: str) -> Path:
    """provisioning.core.domain -> tests/unit/core/domain."""
    return ROOT.joinpath("tests", "unit", *package.split(".")[1:])


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


def count_statements(directory: Path, workdir: Path, config: Path) -> int:
    """Statements in every .py file under directory, imported or not, counted with the same
    coverage settings as the report (config). Coverage's built-in exclusions (``pragma: no cover``,
    ``...`` stubs, ``if TYPE_CHECKING:``) apply only when no settings file replaces them; the
    project's ``exclude_lines = []`` does, so reading it is what makes the two counts agree."""
    if not any(directory.rglob("*.py")):
        raise SystemExit(f"py-cover: {rel(directory)} holds no Python file")
    cov = coverage.Coverage(
        data_file=str(workdir / f".inventory.{directory.name}"),
        source=[str(directory)],
        # Only directory: [tool.coverage.run] source_pkgs would add the whole package whenever it is
        # already imported in this process.
        source_pkgs=[],
        config_file=str(config),
    )
    with warnings.catch_warnings():
        # Nothing runs between start and stop, so coverage warns that it collected no data.
        warnings.simplefilter("ignore", CoverageWarning)
        cov.start()
        cov.stop()
        cov.save()
        report = workdir / f"inventory.{directory.name}.json"
        try:
            cov.json_report(outfile=str(report))
        except NoDataError:
            raise SystemExit(f"py-cover: coverage found no file under {rel(directory)}") from None
    data = json.loads(report.read_text())
    return int(data["totals"]["num_statements"])


def source_digest(package_root: Path) -> str:
    """SHA-256 over the relative path and content of every .py file of the package, in order."""
    digest = hashlib.sha256()
    for path in sorted(package_root.rglob("*.py")):
        digest.update(path.relative_to(package_root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def suite_paths(data_dir: Path, suite: str) -> tuple[Path, Path, Path]:
    """The data, JUnit and stamp files a recording of suite leaves in data_dir."""
    return (
        data_dir / f"{suite}.coverage",
        data_dir / f"{suite}.junit.xml",
        data_dir / f"{suite}.stamp.json",
    )


def stamp(suite: str, data_dir: Path, package_root: Path) -> Path:
    """Start a recording: remove the suite's previous files, then write its stamp."""
    if suite not in RECORDED_SUITES:
        raise GateFailure(
            f"{suite} is not a suite the total combines ({', '.join(RECORDED_SUITES)})"
        )
    data_dir.mkdir(parents=True, exist_ok=True)
    data, junit, stamp_file = suite_paths(data_dir, suite)
    for path in (data, junit, stamp_file):
        path.unlink(missing_ok=True)
    record = {
        "suite": suite,
        "source_sha256": source_digest(package_root),
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    stamp_file.write_text(json.dumps(record, indent=2) + "\n")
    return stamp_file


def junit_outcome(junit: Path) -> str | None:
    """None when the JUnit file shows tests that all passed; else the reason it does not."""
    try:
        root = ElementTree.parse(junit).getroot()  # noqa: S314 - pytest's own output, local file
    except ElementTree.ParseError, OSError:
        return "its JUnit result is unreadable"
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    if not suites:
        return "its JUnit result holds no test suite"
    totals = dict.fromkeys(("tests", "failures", "errors", "skipped"), 0)
    for element in suites:
        for key in totals:
            totals[key] += int(element.get(key, "0"))
    if totals["tests"] == 0:
        return "it ran no test"
    problems = [
        f"{totals['failures']} failed" if totals["failures"] else "",
        f"{totals['errors']} errors" if totals["errors"] else "",
        f"{totals['skipped']} skipped" if totals["skipped"] else "",
    ]
    reasons = [p for p in problems if p]
    return f"its run did not pass ({', '.join(reasons)} of {totals['tests']})" if reasons else None


def check_suite(data_dir: Path, suite: str, package_root: Path, digest: str) -> Path:
    """Return the suite's data file when its recording is complete, passed and current."""
    data, junit, stamp_file = suite_paths(data_dir, suite)
    again = f"record it again with: make {suite} PY_COVER=1"
    missing = [p.name for p in (data, junit, stamp_file) if not p.is_file()]
    if len(missing) == 3:
        raise GateFailure(f"{suite}: no coverage data in {rel(data_dir)}; {again}")
    if missing:
        raise GateFailure(f"{suite}: {', '.join(missing)} missing in {rel(data_dir)}; {again}")
    try:
        recorded = json.loads(stamp_file.read_text())
    except ValueError:
        raise GateFailure(f"{suite}: its stamp is unreadable; {again}") from None
    if not isinstance(recorded, dict) or recorded.get("suite") != suite:
        raise GateFailure(f"{suite}: its stamp names another suite; {again}")
    if recorded.get("source_sha256") != digest:
        raise GateFailure(f"{suite}: the source has changed since it was recorded; {again}")
    outcome = junit_outcome(junit)
    if outcome is not None:
        raise GateFailure(f"{suite}: {outcome}; {again}")
    try:
        measured = CoverageData(basename=str(data))
        measured.read()
        files = measured.measured_files()
    except CoverageException:
        raise GateFailure(f"{suite}: its coverage data is unreadable; {again}") from None
    if not files:
        raise GateFailure(f"{suite}: its coverage data measured no file; {again}")
    outside = sorted(f for f in files if not Path(f).is_relative_to(package_root))
    if outside:
        raise GateFailure(
            f"{suite}: its coverage data names {len(outside)} files outside {rel(package_root)} "
            f"(for example {outside[0]}): recorded in another checkout; {again}"
        )
    return data


def combine(data_files: list[Path], workdir: Path) -> Path:
    """Combine data_files (kept as they are) and return the JSON report of the result."""
    combined = workdir / ".coverage.combined"
    report = workdir / "combined.json"
    cov = coverage.Coverage(data_file=str(combined), config_file=str(PYPROJECT))
    cov.combine([str(p) for p in data_files], strict=True, keep=True)
    cov.save()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", CoverageWarning)
        cov.json_report(outfile=str(report))
    return report


def measured_under(report: Path, directory: Path) -> Measured:
    """Sum the per-file results of the JSON report for the files under directory."""
    data = json.loads(report.read_text())
    statements = covered = 0
    for name, entry in data["files"].items():
        path = Path(name)
        if not path.is_absolute():
            path = Path.cwd() / path
        if path.is_relative_to(directory):
            statements += int(entry["summary"]["num_statements"])
            covered += int(entry["summary"]["covered_lines"])
    return Measured(statements, covered)


def child_env(data_file: Path) -> dict[str, str]:
    """The caller's environment without any coverage or pytest options a recording may have set."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COVERAGE_", "PYTEST_"))}
    env["COVERAGE_FILE"] = str(data_file)
    env["PYTHONASYNCIODEBUG"] = "1"
    return env


def run_pytest(
    targets: list[Path], cov: str | None, marker: str, workdir: Path, label: str
) -> Path:
    """Run pytest on targets with coverage of cov (None: [tool.coverage.run] source_pkgs) and return
    the data file. Raises GateFailure when the run fails or collects no test."""
    data_file = workdir / f".coverage.{label}"
    command = [
        sys.executable,
        "-X",
        "dev",
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-q",
        "-m",
        marker,
        *[rel(t) for t in targets],
        # Plain --cov takes source_pkgs from pyproject.toml; --cov=provisioning on top of it gives
        # the same numbers but a spurious module-not-measured warning (coverage 7.16.1).
        "--cov" if cov is None else f"--cov={cov}",
        "--cov-report=",
    ]
    print(f"py-cover: {' '.join(command[1:])}", flush=True)
    result = subprocess.run(  # noqa: S603 - fixed argv from this script, never through a shell
        command, cwd=ROOT, env=child_env(data_file), timeout=RUN_TIMEOUT_SECONDS, check=False
    )
    if result.returncode == 5:
        raise GateFailure(f"{label}: pytest collected no test in {', '.join(map(rel, targets))}")
    if result.returncode != 0:
        raise GateFailure(f"{label}: pytest exited {result.returncode}")
    return data_file


def check_core(package: str, workdir: Path) -> bool:
    """Return True when the package passes or is empty; print the outcome either way."""
    directory = package_dir(package)
    mirror = mirror_dir(package)
    statements = count_statements(directory, workdir, PYPROJECT)
    if statements == 0:
        print(f"py-cover: {package}: empty (no statements yet)")
        return True
    if not mirror.is_dir() or not any(mirror.rglob("test_*.py")):
        print(
            f"py-cover: {package}: FAILED: {statements} statements but no tests in its mirror "
            f"folder {rel(mirror)} (ruling 10.10)"
        )
        return False
    label = package.replace(".", "_")
    try:
        data_file = run_pytest([mirror], package, "unit", workdir, label)
    except GateFailure as failure:
        print(f"py-cover: {package}: FAILED: {failure}")
        return False
    result = measured_under(combine([data_file], workdir), directory)
    if result.statements != statements:
        print(
            f"py-cover: {package}: FAILED: the report counts {result.statements} statements, "
            f"the inventory {statements}"
        )
        return False
    verdict = "passed" if result.percent >= CORE_MINIMUM else "FAILED"
    print(
        f"py-cover: {package}: {result.percent:.1f}% of {result.statements} statements, credited "
        f"from {rel(mirror)} only (minimum {CORE_MINIMUM:.0f}%): {verdict}"
    )
    return verdict == "passed"


def check_total(workdir: Path) -> bool:
    label = "total, provisioning"
    print(f"py-cover: {label}: combining the suites {', '.join(TOTAL_SUITES)}", flush=True)
    statements = count_statements(PACKAGE_ROOT, workdir, PYPROJECT)
    if statements == 0:
        print(f"py-cover: {label}: empty (no statements yet)")
        return True
    digest = source_digest(PACKAGE_ROOT)
    failures: list[str] = []
    data_files: list[Path] = []
    # The recordings are checked first: a missing one fails without running the unit suite.
    for suite in RECORDED_SUITES:
        try:
            data_files.append(check_suite(DATA_DIR, suite, PACKAGE_ROOT, digest))
        except GateFailure as missing:
            failures.append(str(missing))
    if not failures:
        try:
            data_files.insert(
                0,
                run_pytest(
                    [ROOT / "tests" / "unit", ROOT / "tests" / "contracts"],
                    None,
                    "unit or contract",
                    workdir,
                    "unit",
                ),
            )
        except GateFailure as unit_failure:
            failures.append(str(unit_failure))
    if failures:
        for reason in failures:
            print(f"py-cover: {label}: FAILED: {reason}")
        return False
    result = measured_under(combine(data_files, workdir), PACKAGE_ROOT)
    if result.statements != statements:
        print(
            f"py-cover: {label}: FAILED: the report counts {result.statements} statements, "
            f"the inventory {statements}"
        )
        return False
    verdict = "passed" if result.percent >= TOTAL_MINIMUM else "FAILED"
    print(
        f"py-cover: {label}: {result.percent:.1f}% of {result.statements} statements "
        f"(minimum {TOTAL_MINIMUM:.0f}%): {verdict}"
    )
    return verdict == "passed"


def main(argv: list[str]) -> int:
    if Path.cwd().resolve() != ROOT:
        print(f"py-cover: run from the module root {ROOT}", file=sys.stderr)
        return 2
    if argv[:1] == ["stamp"]:
        if len(argv) != 2:
            print("usage: cover.py stamp SUITE", file=sys.stderr)
            return 2
        try:
            print(
                f"py-cover: recording {argv[1]} into {rel(stamp(argv[1], DATA_DIR, PACKAGE_ROOT))}"
            )
        except GateFailure as failure:
            print(f"py-cover: {failure}", file=sys.stderr)
            return 2
        return 0
    if argv:
        print("usage: cover.py [stamp SUITE]", file=sys.stderr)
        return 2
    workdir = Path(tempfile.mkdtemp(prefix=".coverage-run.", dir=ROOT))
    try:
        results = [check_core(package, workdir) for package in CORE_PACKAGES]
        results.append(check_total(workdir))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if all(results):
        return 0
    print("py-cover: FAILED", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
