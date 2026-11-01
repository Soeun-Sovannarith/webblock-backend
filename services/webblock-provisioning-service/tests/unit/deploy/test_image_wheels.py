"""The runtime image's vendored wheels as written, without a build (decision 0024 G1; TR-32).

The image's build stage installs with ``UV_FROZEN=1``, so it checks each wheel under ``vendor/``
itself: a ``sha256sum -c`` line before ``uv sync``, whose hash must equal the one ``uv.lock``
records for that file and the hash of the committed file. A new wheel without its line, or a
line that disagrees with the lock, fails here before an image is built."""

import hashlib
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[3]
DOCKERFILE = ROOT / "deploy" / "image" / "Dockerfile"
CHECK = re.compile(r"^RUN echo '([0-9a-f]{64})  (vendor/[A-Za-z0-9_.+-]+\.whl)' \| sha256sum -c -$")


def _locked_wheel_hashes() -> dict[str, str]:
    """The SHA-256 ``uv.lock`` records for each wheel it takes from a path under ``vendor/``."""
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    hashes: dict[str, str] = {}
    for package in lock["package"]:
        source = package.get("source", {})
        if not str(source.get("path", "")).startswith("vendor/"):
            continue
        for wheel in package.get("wheels", []):
            hashes["vendor/" + wheel["filename"]] = wheel["hash"].removeprefix("sha256:")
    return hashes


def test_tr32_every_vendored_wheel_is_checked_against_the_lock_before_uv_sync() -> None:
    """TR-32 (decision 0024 G1): one ``sha256sum -c`` line per vendored wheel, after the wheels
    are copied and before ``uv sync``, with the lock's hash, which is the committed file's."""
    lines = DOCKERFILE.read_text().splitlines()
    checks = {m[2]: (i, m[1]) for i, line in enumerate(lines) if (m := CHECK.match(line))}
    locked = _locked_wheel_hashes()
    vendored = {f"vendor/{p.name}" for p in (ROOT / "vendor").glob("*.whl")}
    assert locked, "uv.lock records no vendored wheel"
    assert set(checks) == set(locked) == vendored
    copy = lines.index("COPY vendor/ /build/vendor/")
    sync = next(i for i, line in enumerate(lines) if line.startswith("RUN uv sync"))
    for path, (index, digest) in checks.items():
        assert copy < index < sync, path
        assert digest == locked[path], path
        assert digest == hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), path


def test_tr32_the_check_line_shape_refuses_a_second_space_or_a_path_outside_vendor() -> None:
    """TR-32: the pattern the test reads is strict, so a line it cannot read is not silently
    skipped: ``sha256sum`` needs two spaces, and only ``vendor/`` wheels are checked."""
    digest = "0" * 64
    assert CHECK.match(f"RUN echo '{digest}  vendor/a-1.0-py3-none-any.whl' | sha256sum -c -")
    assert not CHECK.match(f"RUN echo '{digest} vendor/a.whl' | sha256sum -c -")
    assert not CHECK.match(f"RUN echo '{digest}  /tmp/a.whl' | sha256sum -c -")
