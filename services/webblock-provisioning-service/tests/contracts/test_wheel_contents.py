"""The wheel built with the pinned uv ships the package and its type marker, and nothing else
(review-port-arch.md M5, partly).

The wheel is built into a temporary directory with bin/uv, offline, through uv's built-in fast path
for the uv_build backend. Including src/contracts in the wheel (as provisioning/_contracts, M5) is
task P3's; this test then gains that assertion.
"""

import subprocess
import tomllib
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path

type EnvFactory = Callable[[Mapping[str, str] | None], dict[str, str]]


def uv_env(repo_root: Path, make_env: EnvFactory) -> dict[str, str]:
    """The uv environment of mk/python.mk, with exclude-newer read from pyproject.toml."""
    with (repo_root / "pyproject.toml").open("rb") as f:
        exclude_newer = str(tomllib.load(f)["tool"]["uv"]["exclude-newer"])
    return make_env(
        {
            "UV_CACHE_DIR": str(repo_root / "bin" / "uv-cache"),
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_PYTHON_PREFERENCE": "only-system",
            "UV_PYTHON": "python3.14",
            "UV_NO_CONFIG": "1",
            "UV_NO_BUILD": "1",
            "UV_EXCLUDE_NEWER": exclude_newer,
        }
    )


def build_wheel(repo_root: Path, out_dir: Path, make_env: EnvFactory) -> Path:
    result = subprocess.run(
        [
            str(repo_root / "bin" / "uv"),
            "build",
            "--wheel",
            "--offline",
            "--no-create-gitignore",
            "--out-dir",
            str(out_dir),
        ],
        cwd=repo_root,
        env=uv_env(repo_root, make_env),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = sorted(out_dir.glob("*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


def test_the_wheel_holds_the_package_and_its_type_marker_only(
    repo_root: Path, tmp_path: Path, make_env: EnvFactory
) -> None:
    wheel = build_wheel(repo_root, tmp_path / "dist", make_env)
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()

    assert "provisioning/py.typed" in names
    dist_info = "webblock_provisioning-0.0.0.dist-info/"
    outside = [n for n in names if not n.startswith(("provisioning/", dist_info))]
    assert outside == []
    forbidden = [
        n
        for n in names
        if "tests/" in n
        or "conftest" in n
        or ".venv" in n
        or "__pycache__" in n
        or n.endswith(".pyc")
    ]
    assert forbidden == []

    sources = sorted(
        "provisioning/" + p.relative_to(repo_root / "src" / "provisioning").as_posix()
        for p in (repo_root / "src" / "provisioning").rglob("*.py")
    )
    shipped = sorted(n for n in names if n.endswith(".py"))
    assert shipped == sources
