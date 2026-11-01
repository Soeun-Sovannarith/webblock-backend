"""scripts/py/age.py, make py-age (task H1): every pin of uv.lock and every tool version pinned for
scripts/py/tools.sh and scripts/kind-tools.sh is at least 14 days old, unless one waiver in
scripts/py/age-waivers.toml names it with a reason, an approver and an expiry.

A pin's age is that of the newest file of it that uv.lock installs, as PyPI reports the upload
(uv's exclude-newer also works per file). A tool's age is its GitHub release date. The dates come
from the network; a failed lookup fails the gate.
"""

import importlib.util
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "py" / "age.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("py_age_gate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


age = load()

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
OLD = "2026-09-01T10:00:00.000Z"
YOUNG = "2026-10-01T10:00:00.000Z"

LOCK = f"""\
version = 1

[[package]]
name = "webblock-provisioning"
version = "0.0.0"
source = {{ editable = "." }}

[[package]]
name = "old-pkg"
version = "1.0"
source = {{ registry = "https://pypi.org/simple" }}
sdist = {{ url = "https://x/old.tar.gz", hash = "sha256:aa", size = 1, upload-time = "{OLD}" }}
wheels = [
    {{ url = "https://x/old.whl", hash = "sha256:ab", size = 1, upload-time = "{OLD}" }},
]

[[package]]
name = "young-pkg"
version = "2.0"
source = {{ registry = "https://pypi.org/simple" }}
wheels = [
    {{ url = "https://x/young.whl", hash = "sha256:cc", size = 1, upload-time = "{YOUNG}" }},
]
"""


def pypi_document(files: list[tuple[str, str]]) -> dict[str, object]:
    return {
        "urls": [
            {"filename": f"f{n}", "digests": {"sha256": digest}, "upload_time_iso_8601": when}
            for n, (digest, when) in enumerate(files)
        ]
    }


PYPI = {
    "https://pypi.org/pypi/old-pkg/1.0/json": pypi_document([("aa", OLD), ("ab", OLD)]),
    "https://pypi.org/pypi/young-pkg/2.0/json": pypi_document([("cc", YOUNG), ("dd", OLD)]),
}


def fetcher(documents: dict[str, dict[str, object]]) -> Callable[[str], dict[str, object]]:
    def fetch(url: str) -> dict[str, object]:
        if url not in documents:
            raise age.AgeError(f"lookup failed: {url}")
        return documents[url]

    return fetch


def lock(tmp_path: Path, text: str = LOCK) -> Path:
    path = tmp_path / "uv.lock"
    path.write_text(text)
    return path


def test_the_lock_lists_registry_pins_with_their_files(tmp_path: Path) -> None:
    pins = age.read_lock(lock(tmp_path))
    assert [(p.name, p.version, sorted(p.files)) for p in pins] == [
        ("old-pkg", "1.0", ["aa", "ab"]),
        ("young-pkg", "2.0", ["cc"]),
    ]


def test_a_pin_is_dated_by_its_newest_locked_file(tmp_path: Path) -> None:
    pins = age.read_lock(lock(tmp_path))
    dated = [age.date_pypi_pin(p, fetcher(PYPI)) for p in pins]
    assert [d.published for d in dated] == [age.parse_time(OLD), age.parse_time(YOUNG)]


def test_a_locked_file_unknown_to_pypi_fails(tmp_path: Path) -> None:
    documents = dict(PYPI)
    documents["https://pypi.org/pypi/young-pkg/2.0/json"] = pypi_document([("dd", OLD)])
    young = age.read_lock(lock(tmp_path))[1]
    with pytest.raises(age.AgeError, match=r"young-pkg 2\.0: 1 locked files are not on PyPI"):
        age.date_pypi_pin(young, fetcher(documents))


def test_an_upload_time_that_differs_from_the_lock_fails(tmp_path: Path) -> None:
    documents = dict(PYPI)
    documents["https://pypi.org/pypi/young-pkg/2.0/json"] = pypi_document([("cc", OLD)])
    young = age.read_lock(lock(tmp_path))[1]
    with pytest.raises(age.AgeError, match=r"young-pkg 2\.0: PyPI dates a locked file"):
        age.date_pypi_pin(young, fetcher(documents))


def test_a_failed_lookup_fails(tmp_path: Path) -> None:
    old = age.read_lock(lock(tmp_path))[0]
    with pytest.raises(age.AgeError, match="lookup failed"):
        age.date_pypi_pin(old, fetcher({}))


VERSIONS = """\
# comment
UV_VERSION=0.12.18
KIND_VERSION="v0.33.0"
OTHER=1
"""


def test_tool_pins_are_read_from_the_version_files(tmp_path: Path) -> None:
    path = tmp_path / "versions.sh"
    path.write_text(VERSIONS)
    assert age.read_shell_values(path) == {
        "UV_VERSION": "0.12.18",
        "KIND_VERSION": "v0.33.0",
        "OTHER": "1",
    }


def test_a_tool_pin_missing_from_its_version_file_fails(tmp_path: Path) -> None:
    path = tmp_path / "versions.sh"
    path.write_text("UV_VERSION=0.12.18\n")
    with pytest.raises(age.AgeError, match="KIND_VERSION is not set"):
        age.tool_pins([(path, (("kind", "KIND_VERSION", "kubernetes-sigs/kind"),))])


def test_a_tool_is_dated_by_its_github_release() -> None:
    pin = age.ToolPin("kind", "v0.33.0", "kubernetes-sigs/kind")
    documents: dict[str, dict[str, object]] = {
        "https://api.github.com/repos/kubernetes-sigs/kind/releases/tags/v0.33.0": {
            "tag_name": "v0.33.0",
            "published_at": "2026-08-20T17:00:00Z",
        }
    }
    dated = age.date_tool_pin(pin, fetcher(documents))
    assert dated.published == datetime(2026, 8, 20, 17, 0, tzinfo=UTC)


def dated(name: str, days: float) -> object:
    return age.Dated("pypi", name, "1.0", NOW - timedelta(days=days))


def waiver(name: str, expires: date, kind: str = "pypi") -> object:
    return age.Waiver(kind, name, "1.0", "prototype pin", "the lead", expires)


# --- the 14-day rule (finding F-3 of H-V) ---------------------------------------------------------
# Decision 0013 section 9, S6 (and TECH_STACK.md): a dependency version is adopted only when it is
# at least 14 days old. The whole gate (main, with the network lookup replaced) is fed one pin just
# younger and one just older than 14 days, so a changed limit fails these tests.


def one_pin_lock(published: datetime) -> str:
    when = published.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return (
        'version = 1\n\n[[package]]\nname = "pin"\nversion = "1.0"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
        'wheels = [{ url = "https://x/pin.whl", hash = "sha256:ee", size = 1, '
        f'upload-time = "{when}" }}]\n'
    )


@pytest.mark.parametrize(("days", "status"), [(13.9, 1), (14.1, 0)])
def test_the_gate_requires_14_days(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    days: float,
    status: int,
) -> None:
    published = datetime.now(UTC) - timedelta(days=days)
    when = published.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    document = pypi_document([("ee", when)])
    monkeypatch.setattr(age, "LOCK", lock(tmp_path, one_pin_lock(published)))
    monkeypatch.setattr(age, "TOOL_FILES", ())
    monkeypatch.setattr(age, "WAIVERS", tmp_path / "no-waivers.toml")
    monkeypatch.setattr(
        age, "fetch_json", fetcher({"https://pypi.org/pypi/pin/1.0/json": document})
    )
    monkeypatch.chdir(age.ROOT)
    assert age.main() == status
    out = capsys.readouterr().out
    assert "minimum 14 days" in out
    assert ("younger than 14 days and no waiver" in out) is (status == 1)


def test_a_young_pin_without_a_waiver_fails() -> None:
    """Planted defect: a pin published 13 days ago."""
    failures, _ = age.evaluate([dated("old", 30), dated("young", 13)], [], NOW, 14)
    assert failures == [
        "pypi young 1.0: published 2026-09-27, 13.0 days ago, younger than 14 days and no waiver"
    ]


def test_a_young_pin_with_a_current_waiver_passes() -> None:
    failures, notes = age.evaluate(
        [dated("young", 13)], [waiver("young", date(2026, 10, 12))], NOW, 14
    )
    assert failures == []
    assert notes == [
        "pypi young 1.0: 13.0 days old, waived until 2026-10-12: prototype pin (the lead)"
    ]


def test_an_expired_waiver_fails() -> None:
    failures, _ = age.evaluate([dated("young", 13)], [waiver("young", date(2026, 10, 9))], NOW, 14)
    assert failures == ["pypi young 1.0: its waiver expired on 2026-10-09"]


def test_a_current_waiver_that_matches_no_young_pin_is_reported_not_failed() -> None:
    """A waived pin ages past 14 days before its waiver expires; that is not a fault."""
    failures, notes = age.evaluate([dated("old", 30)], [waiver("old", date(2026, 10, 30))], NOW, 14)
    assert failures == []
    assert notes == ["pypi old 1.0: the waiver is no longer needed (no young pin); remove it"]


def test_an_expired_waiver_fails_even_when_no_pin_needs_it() -> None:
    failures, _ = age.evaluate([dated("old", 30)], [waiver("old", date(2026, 10, 9))], NOW, 14)
    assert failures == ["pypi old 1.0: its waiver expired on 2026-10-09; remove it"]


def test_the_waiver_file_is_read_and_checked(tmp_path: Path) -> None:
    path = tmp_path / "age-waivers.toml"
    path.write_text(
        '[[waiver]]\nkind = "tool"\nname = "grype"\nversion = "v1"\n'
        'reason = "r"\napproved_by = "a"\nexpires = 2026-10-20\n'
    )
    assert age.load_waivers(path) == [
        age.Waiver("tool", "grype", "v1", "r", "a", date(2026, 10, 20))
    ]


COMPLETE = {
    "kind": '"pypi"',
    "name": '"x"',
    "version": '"1"',
    "reason": '"r"',
    "approved_by": '"a"',
    "expires": "2026-10-20",
}


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"reason": None}, "reason"),
        ({"approved_by": None}, "approved_by"),
        ({"expires": None}, "expires"),
        ({"kind": '"npm"'}, "kind"),
        ({"reason": '""'}, "reason"),
        ({"expires": '"soon"'}, "expires"),
    ],
)
def test_an_incomplete_waiver_is_refused(
    tmp_path: Path, change: dict[str, str | None], reason: str
) -> None:
    fields = {**COMPLETE, **change}
    body = "".join(f"{k} = {v}\n" for k, v in fields.items() if v is not None)
    path = tmp_path / "age-waivers.toml"
    path.write_text("[[waiver]]\n" + body)
    with pytest.raises(age.AgeError, match=reason):
        age.load_waivers(path)


DIGEST = "51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d"
IMAGE = f"docker.io/library/python:3.14.7-slim-trixie@sha256:{DIGEST}"
PYTHON_IMAGE = (("python", "BASE_IMAGE", "BASE_IMAGE_PUBLISHED"),)


def test_a_base_image_is_dated_by_its_recorded_publication(tmp_path: Path) -> None:
    """Decision 0016 section 9 ruling 4: the base image is pinned by digest and at least 14 days
    old; its publication is recorded with the pin (deploy/image/versions.sh)."""
    path = tmp_path / "versions.sh"
    path.write_text(f'BASE_IMAGE="{IMAGE}"\nBASE_IMAGE_PUBLISHED="2026-09-25T20:08:39Z"\n')
    assert age.image_pins([(path, PYTHON_IMAGE)]) == [
        age.Dated(
            "image",
            "python",
            f"3.14.7-slim-trixie@sha256:{DIGEST}",
            datetime(2026, 9, 25, 20, 8, 39, tzinfo=UTC),
        )
    ]


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (
            'BASE_IMAGE="docker.io/library/python:3.14.7-slim-trixie"\n'
            'BASE_IMAGE_PUBLISHED="2026-09-25T20:08:39Z"\n',
            "not <name>:<tag>@sha256:<digest>",
        ),
        (
            f'BASE_IMAGE="docker.io/library/python@sha256:{DIGEST}"\n'
            'BASE_IMAGE_PUBLISHED="2026-09-25T20:08:39Z"\n',
            "not <name>:<tag>@sha256:<digest>",
        ),
        (f'BASE_IMAGE="{IMAGE}"\n', "BASE_IMAGE_PUBLISHED is not set"),
        (f'BASE_IMAGE="{IMAGE}"\nBASE_IMAGE_PUBLISHED="last-week"\n', "is not a timestamp"),
    ],
)
def test_a_wrong_image_pin_fails(tmp_path: Path, text: str, reason: str) -> None:
    path = tmp_path / "versions.sh"
    path.write_text(text)
    with pytest.raises(age.AgeError, match=reason):
        age.image_pins([(path, PYTHON_IMAGE)])


def test_a_young_image_fails_unless_a_waiver_names_it() -> None:
    young = age.Dated(
        "image", "python", f"3.14.8-slim-trixie@sha256:{'a' * 64}", NOW - timedelta(days=6)
    )
    failures, _ = age.evaluate([young], [], NOW, 14)
    assert failures == [
        f"image python {young.version}: published 2026-10-04, 6.0 days ago, younger than 14 "
        "days and no waiver"
    ]
    waiver = age.Waiver("image", "python", young.version, "r", "the owner", date(2026, 10, 20))
    failures, notes = age.evaluate([young], [waiver], NOW, 14)
    assert failures == []
    assert len(notes) == 1


def test_an_image_waiver_is_read(tmp_path: Path) -> None:
    path = tmp_path / "age-waivers.toml"
    path.write_text(
        '[[waiver]]\nkind = "image"\nname = "python"\nversion = "3.14.8-slim-trixie@sha256:x"\n'
        'reason = "r"\napproved_by = "a"\nexpires = 2026-10-20\n'
    )
    assert age.load_waivers(path) == [
        age.Waiver("image", "python", "3.14.8-slim-trixie@sha256:x", "r", "a", date(2026, 10, 20))
    ]


def test_tr44_the_age_gate_dates_the_k3d_pin_by_its_github_release() -> None:
    """TR-44 (spike G2): bin/k3d is pinned in deploy/kind/versions.sh and the 14-day rule dates it
    from the k3d-io/k3d release."""
    pins = {pin.name: pin for pin in age.tool_pins(age.TOOL_FILES)}
    assert pins["k3d"] == age.ToolPin("k3d", "v5.9.0", "k3d-io/k3d")


def test_the_repository_pins_buildx_and_the_base_image() -> None:
    assert "docker-buildx" in {pin.name for pin in age.tool_pins(age.TOOL_FILES)}
    (base,) = age.image_pins(age.IMAGE_FILES)
    assert base.version == f"3.14.7-slim-trixie@sha256:{DIGEST}"


def test_the_gate_dates_the_image_pins_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """main feeds the recorded image publication through the same rule: a base image published
    three days ago fails with no lookup at all."""
    published = (datetime.now(UTC) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    versions = tmp_path / "versions.sh"
    versions.write_text(f'BASE_IMAGE="{IMAGE}"\nBASE_IMAGE_PUBLISHED="{published}"\n')
    monkeypatch.setattr(age, "LOCK", lock(tmp_path, "version = 1\n"))
    monkeypatch.setattr(age, "TOOL_FILES", ())
    monkeypatch.setattr(age, "IMAGE_FILES", ((versions, PYTHON_IMAGE),))
    monkeypatch.setattr(age, "WAIVERS", tmp_path / "no-waivers.toml")
    monkeypatch.setattr(age, "fetch_json", fetcher({}))
    monkeypatch.chdir(age.ROOT)
    assert age.main() == 1
    out = capsys.readouterr().out
    assert "image python 3.14.7-slim-trixie" in out
    assert "1 image pins" in out
