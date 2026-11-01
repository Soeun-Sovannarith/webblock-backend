"""Suite rules for every Python test (decision 0013 section 10.9).

- Each test gets the marker of the folder it lives in; a test outside the known folders is refused.
- In every suite that needs a real dependency (all but unit and contract) a skip or an xfail is a
  failure: a missing server or cluster must fail the run, never pass it quietly.
- No ``__init__.py`` may exist under ``tests/``: pytest runs with ``--import-mode=importlib`` and
  the test folders are not packages.
- Real-cluster safety (owner's rule, CLAUDE.md section 0): Kubernetes client libraries fix their
  default kubeconfig from ``$KUBECONFIG`` or ``~/.kube/config`` when first imported. Before any
  test module can import one, ``KUBECONFIG`` is set here to a path that never exists, so no code
  path in any test process can fall back to the owner's kubeconfig. Tests that use Kind pass the
  project-local kubeconfig explicitly. Found by prototype task D on 8 October 2026.
"""

import os
from collections.abc import Generator
from pathlib import Path

import pytest

POISONED_KUBECONFIG = "/nonexistent/webblock-tests-never-read-a-default-kubeconfig"
os.environ["KUBECONFIG"] = POISONED_KUBECONFIG

TESTS_ROOT = Path(__file__).resolve().parent

# Folder (relative to tests/) to marker. The longest matching prefix wins, so a test under
# tests/integration/catalog is in the catalog_live suite, not the integration suite.
SUITES: dict[tuple[str, ...], str] = {
    ("unit",): "unit",
    ("contracts",): "contract",
    ("integration",): "integration",
    ("integration", "jenkins"): "jenkins",
    ("integration", "kubernetes"): "kind",
    ("integration", "policy"): "kind",
    ("integration", "stack"): "kind",
    ("integration", "catalog"): "catalog_live",
    ("messaging",): "messaging",
    ("e2e",): "e2e",
    ("chart",): "chart",
}

# Suites that need a real dependency: a skip or an xfail there is reported as a failure.
NO_SKIP_SUITES = frozenset(
    {
        "integration",
        "jenkins",
        "kind",
        "catalog_live",
        "messaging",
        "e2e",
        "chart",
    }
)


def suite_of(path: Path) -> str | None:
    """Return the suite marker for a path under tests/, or None when it is in no known suite."""
    try:
        parts = path.resolve().relative_to(TESTS_ROOT).parts
    except ValueError:
        return None
    best: tuple[str, ...] = ()
    for prefix in SUITES:
        if parts[: len(prefix)] == prefix and len(prefix) > len(best):
            best = prefix
    return SUITES[best] if best else None


def pytest_sessionstart(session: pytest.Session) -> None:
    packages = sorted(
        str(p.relative_to(TESTS_ROOT.parent)) for p in TESTS_ROOT.rglob("__init__.py")
    )
    if packages:
        raise pytest.UsageError(
            "no __init__.py may exist under tests/ (decision 0013 section 10.9): "
            + ", ".join(packages)
        )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    outside: list[str] = []
    for item in items:
        suite = suite_of(item.path)
        if suite is None:
            outside.append(item.nodeid)
            continue
        item.add_marker(getattr(pytest.mark, suite))
    if outside:
        raise pytest.UsageError(
            "tests must live under tests/unit, tests/contracts, tests/integration, "
            "tests/messaging, tests/e2e or tests/chart: " + ", ".join(outside)
        )


def _no_skip_message(
    where: str, suite: str, report: pytest.TestReport | pytest.CollectReport
) -> str:
    reason = getattr(report, "wasxfail", None) or str(report.longrepr)
    return (
        f"{where} in the {suite} suite was skipped or marked xfail, which counts as a failure "
        f"(decision 0013 section 10.9): {reason}"
    )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    suite = suite_of(item.path)
    if (
        suite is not None
        and suite in NO_SKIP_SUITES
        and (report.skipped or hasattr(report, "wasxfail"))
    ):
        report.longrepr = _no_skip_message(item.nodeid, suite, report)
        report.outcome = "failed"
        if hasattr(report, "wasxfail"):
            del report.wasxfail
    return report


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(
    collector: pytest.Collector,
) -> Generator[None, pytest.CollectReport, pytest.CollectReport]:
    report = yield
    suite = suite_of(collector.path)
    if suite is not None and suite in NO_SKIP_SUITES and report.skipped:
        report.longrepr = _no_skip_message(collector.nodeid, suite, report)
        report.outcome = "failed"
    return report
