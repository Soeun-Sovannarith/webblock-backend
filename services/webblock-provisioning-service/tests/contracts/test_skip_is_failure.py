"""tests/conftest.py makes a skip or an xfail in every suite that needs a real dependency a
failure, applies markers by folder, and refuses an __init__.py under tests/ and a test outside the
suite folders (decision 0013 section 10.9, TR-2).

Each case builds a throwaway project from the real pyproject.toml and tests/conftest.py, writes one
planted test file into it, and runs pytest on it in a subprocess.
"""

import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

type EnvFactory = Callable[[Mapping[str, str] | None], dict[str, str]]

SKIP_IN_TEST = (
    "import pytest\n\n\ndef test_needs_a_server() -> None:\n    pytest.skip('no server')\n"
)
SKIP_MODULE = (
    "import pytest\n\npytest.skip('no broker', allow_module_level=True)\n\n\n"
    "def test_x() -> None:\n    pass\n"
)
SKIPIF = (
    "import pytest\n\n\n@pytest.mark.skipif(True, reason='no cluster')\n"
    "def test_x() -> None:\n    pass\n"
)
XFAIL = (
    "import pytest\n\n\n@pytest.mark.xfail(reason='flaky')\n"
    "def test_x() -> None:\n    raise AssertionError\n"
)
XPASS = "import pytest\n\n\n@pytest.mark.xfail(reason='flaky')\ndef test_x() -> None:\n    pass\n"
SKIP_IN_FIXTURE = (
    "import pytest\n\n\n@pytest.fixture\ndef server() -> None:\n    pytest.skip('no server')\n\n\n"
    "def test_x(server: None) -> None:\n    pass\n"
)
MARKER_ECHO = (
    "import pytest\n\n\ndef test_marker(request: pytest.FixtureRequest) -> None:\n"
    "    print('MARKERS=' + ','.join(sorted(m.name for m in request.node.iter_markers())))\n"
)

NO_SKIP_MESSAGE = "which counts as a failure"


def run_project(
    tmp_path: Path, repo_root: Path, venv_bin: Path, make_env: EnvFactory, files: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    shutil.copy2(repo_root / "pyproject.toml", project / "pyproject.toml")
    shutil.copy2(repo_root / "tests" / "conftest.py", project / "tests" / "conftest.py")
    for relative, content in files.items():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return subprocess.run(
        [str(venv_bin / "python"), "-X", "dev", "-m", "pytest", "-p", "no:cacheprovider", "-s"],
        cwd=project,
        env=make_env(None),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize(
    ("relative", "content"),
    [
        ("tests/integration/test_planted.py", SKIP_IN_TEST),
        ("tests/integration/test_planted.py", SKIP_IN_FIXTURE),
        ("tests/integration/test_planted.py", XPASS),
        ("tests/messaging/test_planted.py", SKIP_MODULE),
        ("tests/messaging/test_planted.py", XFAIL),
        ("tests/integration/kubernetes/test_planted.py", SKIPIF),
        ("tests/integration/catalog/test_planted.py", SKIP_IN_TEST),
        ("tests/e2e/test_planted.py", SKIPIF),
        ("tests/chart/test_planted.py", XFAIL),
    ],
)
def test_a_skip_or_xfail_in_a_dependency_suite_fails_the_run(
    tmp_path: Path,
    repo_root: Path,
    venv_bin: Path,
    make_env: EnvFactory,
    relative: str,
    content: str,
) -> None:
    result = run_project(tmp_path, repo_root, venv_bin, make_env, {relative: content})
    output = result.stdout + result.stderr
    assert result.returncode in (1, 2), output
    assert NO_SKIP_MESSAGE in output, output


@pytest.mark.parametrize("content", [SKIP_IN_TEST, SKIP_MODULE, XFAIL])
def test_a_skip_in_the_unit_suite_stays_a_skip(
    tmp_path: Path, repo_root: Path, venv_bin: Path, make_env: EnvFactory, content: str
) -> None:
    files = {"tests/unit/test_planted.py": content, "tests/unit/test_passes.py": MARKER_ECHO}
    result = run_project(tmp_path, repo_root, venv_bin, make_env, files)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert NO_SKIP_MESSAGE not in output, output
    assert "1 passed" in output, output


@pytest.mark.parametrize(
    ("relative", "marker"),
    [
        ("tests/unit/core/domain/test_planted.py", "unit"),
        ("tests/contracts/test_planted.py", "contract"),
        ("tests/integration/test_planted.py", "integration"),
        ("tests/messaging/test_planted.py", "messaging"),
        ("tests/integration/jenkins/test_planted.py", "jenkins"),
        ("tests/integration/kubernetes/test_planted.py", "kind"),
        ("tests/integration/catalog/test_planted.py", "catalog_live"),
        ("tests/e2e/test_planted.py", "e2e"),
        ("tests/chart/test_planted.py", "chart"),
    ],
)
def test_each_folder_applies_its_marker(
    tmp_path: Path,
    repo_root: Path,
    venv_bin: Path,
    make_env: EnvFactory,
    relative: str,
    marker: str,
) -> None:
    result = run_project(tmp_path, repo_root, venv_bin, make_env, {relative: MARKER_ECHO})
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert f"MARKERS={marker}\n" in output, output


def test_an_init_file_under_tests_is_refused(
    tmp_path: Path, repo_root: Path, venv_bin: Path, make_env: EnvFactory
) -> None:
    result = run_project(
        tmp_path,
        repo_root,
        venv_bin,
        make_env,
        {"tests/unit/__init__.py": "", "tests/unit/test_planted.py": MARKER_ECHO},
    )
    output = result.stdout + result.stderr
    assert result.returncode == 4, output
    assert "no __init__.py may exist under tests/" in output, output
    assert "tests/unit/__init__.py" in output, output


@pytest.mark.parametrize(
    "relative",
    [
        "tests/elsewhere/test_planted.py",
        "tests/acceptance/test_planted.py",
        "tests/acceptance/catalog/test_planted.py",
    ],
)
def test_a_test_outside_the_suite_folders_is_refused(
    tmp_path: Path, repo_root: Path, venv_bin: Path, make_env: EnvFactory, relative: str
) -> None:
    """TR-2: tests/acceptance and tests/acceptance/catalog (the Go reference's suites) are no
    suite folders any more: a test there is refused like any other stray test."""
    result = run_project(tmp_path, repo_root, venv_bin, make_env, {relative: MARKER_ECHO})
    output = result.stdout + result.stderr
    assert result.returncode == 4, output
    assert "tests must live under" in output, output
