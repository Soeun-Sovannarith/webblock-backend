"""scripts/py/image_scan.py, make image-scan (task H1): Grype on the runtime images against one
verified offline database per run.

- The database listing must name a v6 archive by a plain file name with a SHA-256, built at most 14
  days ago; the archive must match that SHA-256, and Grype's own status after the import must agree.
- Any High or Critical match fails unless a reviewed, time-bounded exception names it (image,
  vulnerability, package) with a reason, a reviewer, an expiry and a follow-up. An expired exception
  fails; one that expires more than 30 days ahead is refused.
"""

import importlib.util
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "py" / "image_scan.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("py_image_scan_gate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scan = load()

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
SHA = "10771a4ded5ec61df357f3bdc3d92807aedf23a717bcd2395c9decfdee20524f"


def listing(**change: object) -> dict[str, object]:
    document: dict[str, object] = {
        "status": "active",
        "schemaVersion": "v6.1.10",
        "built": "2026-10-09T06:32:32Z",
        "path": "vulnerability-db_v6.1.10_2026-10-09T00:35:49Z_1791527552.tar.zst",
        "checksum": f"sha256:{SHA}",
    }
    document.update(change)
    return document


def test_a_fresh_listing_is_accepted() -> None:
    accepted = scan.validate_listing(listing(), NOW, 14)
    assert accepted.archive == "vulnerability-db_v6.1.10_2026-10-09T00:35:49Z_1791527552.tar.zst"
    assert accepted.sha256 == SHA
    assert accepted.built == datetime(2026, 10, 9, 6, 32, 32, tzinfo=UTC)


def test_a_stale_database_is_refused() -> None:
    """Planted defect: the listed database was built 15 days ago."""
    with pytest.raises(scan.ScanError, match="older than the limit of 14 days"):
        scan.validate_listing(listing(built="2026-09-25T06:00:00Z"), NOW, 14)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"path": "../evil.tar.zst"}, "unexpected archive name"),
        ({"path": "https://elsewhere/db.tar.zst"}, "unexpected archive name"),
        ({"checksum": "md5:abcd"}, "no SHA-256"),
        ({"schemaVersion": "v5.0.1"}, "schema"),
        ({"status": "deprecated"}, "not active"),
        ({"built": "2026-10-13T00:00:00Z"}, "in the future"),
        ({"built": "recently"}, "not a timestamp"),
    ],
)
def test_a_wrong_listing_is_refused(change: dict[str, object], reason: str) -> None:
    with pytest.raises(scan.ScanError, match=reason):
        scan.validate_listing(listing(**change), NOW, 14)


def test_an_archive_that_does_not_match_the_listing_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "db.tar.zst"
    archive.write_bytes(b"not the database")
    with pytest.raises(scan.ScanError, match="SHA-256 mismatch"):
        scan.check_archive(archive, scan.validate_listing(listing(), NOW, 14))


def test_grype_must_report_the_imported_database_as_valid_and_the_same() -> None:
    accepted = scan.validate_listing(listing(), NOW, 14)
    good = {"schemaVersion": "v6.1.10", "built": "2026-10-09T06:32:32Z", "valid": True}
    scan.check_status(good, accepted)
    with pytest.raises(scan.ScanError, match="not valid"):
        scan.check_status({**good, "valid": False}, accepted)
    with pytest.raises(scan.ScanError, match="built"):
        scan.check_status({**good, "built": "2026-10-01T00:00:00Z"}, accepted)


def grype_report(matches: list[tuple[str, str, str, str]]) -> dict[str, object]:
    return {
        "matches": [
            {
                "vulnerability": {"id": ident, "severity": severity, "fix": {"versions": []}},
                "artifact": {"name": package, "version": version, "type": "apk"},
            }
            for ident, severity, package, version in matches
        ]
    }


def test_matches_are_read_from_the_report() -> None:
    found = scan.read_matches(
        grype_report([("CVE-1", "High", "zlib", "1.3"), ("CVE-2", "Low", "tiff", "4.7")])
    )
    assert [(m.ident, m.severity, m.package, m.version) for m in found] == [
        ("CVE-1", "High", "zlib", "1.3"),
        ("CVE-2", "Low", "tiff", "4.7"),
    ]


def accepted(
    vulnerability: str = "CVE-1", package: str = "zlib", expires: date = date(2026, 11, 1)
) -> object:
    return scan.Accepted(
        "sample-site", vulnerability, package, "base image fix pending", "the lead", expires, "bump"
    )


def matches(*rows: tuple[str, str, str]) -> dict[str, list[object]]:
    return {"sample-site": scan.read_matches(grype_report([(i, s, p, "1") for i, s, p in rows]))}


def test_a_high_finding_without_an_exception_fails() -> None:
    failures, _ = scan.evaluate(matches(("CVE-1", "High", "zlib")), [], NOW.date())
    assert failures == ["sample-site: High CVE-1 in zlib 1: no exception"]


def test_a_critical_finding_without_an_exception_fails() -> None:
    failures, _ = scan.evaluate(matches(("CVE-9", "Critical", "openssl")), [], NOW.date())
    assert failures == ["sample-site: Critical CVE-9 in openssl 1: no exception"]


def test_medium_and_lower_findings_do_not_fail() -> None:
    rows = (("CVE-2", "Medium", "a"), ("CVE-3", "Low", "b"), ("CVE-4", "Negligible", "c"))
    failures, _ = scan.evaluate(matches(*rows), [], NOW.date())
    assert failures == []


def test_a_current_exception_accepts_the_finding_it_names() -> None:
    failures, notes = scan.evaluate(matches(("CVE-1", "High", "zlib")), [accepted()], NOW.date())
    assert failures == []
    assert notes == [
        "sample-site: High CVE-1 in zlib 1: accepted until 2026-11-01 by the lead: base image fix "
        "pending (follow-up: bump)"
    ]


def test_an_exception_for_another_package_does_not_cover_the_finding() -> None:
    failures, _ = scan.evaluate(
        matches(("CVE-1", "High", "zlib")), [accepted(package="tiff")], NOW.date()
    )
    assert "sample-site: High CVE-1 in zlib 1: no exception" in failures


def test_an_expired_exception_fails() -> None:
    """Planted defect: the exception expired yesterday."""
    failures, _ = scan.evaluate(
        matches(("CVE-1", "High", "zlib")), [accepted(expires=date(2026, 10, 9))], NOW.date()
    )
    assert failures == ["sample-site CVE-1 zlib: the exception expired on 2026-10-09"]


def test_an_exception_beyond_thirty_days_is_refused() -> None:
    """Decision 0016 section 9 ruling 4 (and section 7 ruling 3): at most 30 days."""
    far = NOW.date() + timedelta(days=31)
    failures, _ = scan.evaluate(
        matches(("CVE-1", "High", "zlib")), [accepted(expires=far)], NOW.date()
    )
    assert failures == [
        f"sample-site CVE-1 zlib: the exception expires {far.isoformat()}, more than 30 days ahead"
    ]


def test_an_exception_of_thirty_days_is_accepted() -> None:
    edge = NOW.date() + timedelta(days=30)
    failures, _ = scan.evaluate(
        matches(("CVE-1", "High", "zlib")), [accepted(expires=edge)], NOW.date()
    )
    assert failures == []


def test_an_unused_exception_is_reported_for_removal() -> None:
    failures, notes = scan.evaluate(matches(), [accepted()], NOW.date())
    assert failures == []
    assert notes == ["sample-site CVE-1 zlib: the exception matches no finding; remove it"]


def test_the_exception_file_is_read(tmp_path: Path) -> None:
    path = tmp_path / "image-scan-exceptions.toml"
    path.write_text(
        '[[exception]]\nimage = "sample-site"\nvulnerability = "CVE-1"\npackage = "zlib"\n'
        'reason = "r"\nreviewer = "the lead"\nexpires = 2026-11-01\nfollow_up = "bump"\n'
    )
    assert scan.load_exceptions(path) == [
        scan.Accepted("sample-site", "CVE-1", "zlib", "r", "the lead", date(2026, 11, 1), "bump")
    ]


@pytest.mark.parametrize(
    "missing", ["image", "vulnerability", "package", "reason", "reviewer", "follow_up"]
)
def test_an_exception_without_a_field_is_refused(tmp_path: Path, missing: str) -> None:
    fields = {
        "image": '"sample-site"',
        "vulnerability": '"CVE-1"',
        "package": '"zlib"',
        "reason": '"r"',
        "reviewer": '"the lead"',
        "expires": "2026-11-01",
        "follow_up": '"bump"',
    }
    del fields[missing]
    path = tmp_path / "image-scan-exceptions.toml"
    path.write_text("[[exception]]\n" + "".join(f"{k} = {v}\n" for k, v in fields.items()))
    with pytest.raises(scan.ScanError, match=missing):
        scan.load_exceptions(path)


def test_an_exception_with_a_quoted_expiry_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "image-scan-exceptions.toml"
    path.write_text(
        '[[exception]]\nimage = "sample-site"\nvulnerability = "CVE-1"\npackage = "zlib"\n'
        'reason = "r"\nreviewer = "a"\nexpires = "2026-11-01"\nfollow_up = "b"\n'
    )
    with pytest.raises(scan.ScanError, match="expires must be a date"):
        scan.load_exceptions(path)


def test_an_exception_for_an_unknown_image_is_refused() -> None:
    other = scan.Accepted("other", "CVE-1", "zlib", "r", "a", date(2026, 11, 1), "b")
    with pytest.raises(scan.ScanError, match="unknown image 'other'"):
        scan.check_exception_images([other], {"sample-site", "clothing-shop"})


DIGEST = "sha256:" + "4" * 64


def test_image_references_name_known_images_pinned_by_digest() -> None:
    """An image the stack's BuildKit built can be scanned by reference instead of a local build
    (the development machine's Docker has no BuildKit for COPY --chmod)."""
    refs = scan.parse_refs(f"sample-site=localhost:5001/webblock/sites/p1@{DIGEST}")
    assert refs == {"sample-site": f"localhost:5001/webblock/sites/p1@{DIGEST}"}
    assert scan.parse_refs("") == {}


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("sample-site=localhost:5001/webblock/sites/p1:latest", "pinned by digest"),
        (f"other=localhost:5001/x@{DIGEST}", "unknown image 'other'"),
        (f"localhost:5001/x@{DIGEST}", "name=reference"),
        (f"sample-site=a/b@{DIGEST} sample-site=a/c@{DIGEST}", "twice"),
    ],
)
def test_a_wrong_image_reference_is_refused(value: str, reason: str) -> None:
    with pytest.raises(scan.ScanError, match=reason):
        scan.parse_refs(value)


def test_the_service_image_is_named_for_references_and_exceptions() -> None:
    ref = f"localhost:5001/webblock/platform/provisioning@{DIGEST}"
    assert scan.parse_refs(f"provisioning={ref}") == {"provisioning": ref}
    granted = scan.Accepted(
        "provisioning", "CVE-1", "zlib", "r", "the owner", date(2026, 11, 1), "b"
    )
    scan.check_exception_images([granted], set(scan.TARGETS))


def test_the_service_image_is_scanned_by_reference_only(tmp_path: Path) -> None:
    """The service image is what the cluster run pushed (bin/kind-state/proto/provisioning/
    image.env); the scan refuses before it downloads anything."""
    with pytest.raises(scan.ScanError, match="scan the image the cluster run pushed"):
        scan.run(tmp_path, ["provisioning"], 14, "/usr/bin/docker", {}, None)
    for untouched in ("db", "home", "tmp"):
        assert not (tmp_path / untouched).exists()


def test_local_builds_use_the_pinned_buildx_when_it_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decision 0016 section 9 ruling 5, finding F-10: the project-local DOCKER_CONFIG holds the
    plugin, on the daemon of the caller's current context."""
    cli = tmp_path / "docker-cli"
    (cli / "cli-plugins").mkdir(parents=True)
    (cli / "cli-plugins" / "docker-buildx").write_text("")
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\necho unix:///run/user/1000/docker.sock\n")
    docker.chmod(0o755)
    monkeypatch.setattr(scan, "DOCKER_CLI", cli)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    env = scan.docker_env(str(docker))
    assert env is not None
    assert env["DOCKER_CONFIG"] == str(cli)
    assert env["DOCKER_HOST"] == "unix:///run/user/1000/docker.sock"


def test_local_builds_keep_the_callers_docker_without_buildx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scan, "DOCKER_CLI", tmp_path / "absent")
    assert scan.docker_env("/usr/bin/docker") is None


def test_the_build_the_save_and_the_removal_use_that_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, object]] = []

    def run_command(command: list[str], timeout: int, what: str, **kwargs: object) -> None:
        if command[0] == str(scan.GRYPE):
            raise scan.ScanError("stop before the scan")
        seen.append((command[1], kwargs.get("env")))

    def removal(command: list[str], **kwargs: object) -> None:
        seen.append((command[1], kwargs.get("env")))

    env = {"DOCKER_CONFIG": "/project/bin/kind-state/docker-cli"}
    monkeypatch.setattr(scan, "docker_env", lambda docker: env)
    monkeypatch.setattr(scan, "run_command", run_command)
    monkeypatch.setattr(scan.subprocess, "run", removal)
    with pytest.raises(scan.ScanError, match="stop before the scan"):
        scan.build_and_scan("sample-site", tmp_path, "/usr/bin/docker")
    assert seen == [("build", env), ("save", env), ("image", env)]


def test_the_service_image_is_scanned_when_a_reference_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ref = f"localhost:5001/webblock/platform/provisioning@{DIGEST}"
    scanned: list[str] = []

    def scan_reference(name: str, got: str, work: Path, ca: Path | None) -> list[object]:
        scanned.append(f"{name}={got}")
        return []

    monkeypatch.setattr(scan, "load_exceptions", lambda path: [])
    monkeypatch.setattr(scan, "prepare_database", lambda work, days: None)
    monkeypatch.setattr(scan, "scan_reference", scan_reference)
    status = scan.run(tmp_path, ["provisioning"], 14, None, {"provisioning": ref}, None)
    assert status == 0
    assert scanned == [f"provisioning={ref}"]
    assert "provisioning: no finding" in capsys.readouterr().out
