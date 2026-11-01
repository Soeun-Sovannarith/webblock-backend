"""scripts/py/vuln.py, make py-vuln (task H1, OD-57): osv-scanner offline on uv.lock against one
verified download of the OSV PyPI advisories per run.

The download is accepted only when the transfer matches the md5 and length Google Cloud Storage
sends, every entry is one advisory named by its id, the archive holds enough of them, and the newest
advisory is at most 14 days old. Anything else is refused, so the scan never runs on a stale,
empty or damaged copy.
"""

import base64
import hashlib
import importlib.util
import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "py" / "vuln.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("py_vuln_gate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vuln = load()

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def advisory(ident: str, modified: datetime, ecosystem: str = "PyPI") -> dict[str, object]:
    return {
        "id": ident,
        "modified": modified.isoformat().replace("+00:00", "Z"),
        "affected": [{"package": {"ecosystem": ecosystem, "name": "requests"}}],
    }


def archive(tmp_path: Path, entries: dict[str, object], name: str = "all.zip") -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        for entry, content in entries.items():
            zf.writestr(entry, content if isinstance(content, str) else json.dumps(content))
    return path


def fresh(count: int, newest: datetime = NOW - timedelta(hours=3)) -> dict[str, object]:
    entries: dict[str, object] = {
        f"PYSEC-2020-{n}.json": advisory(f"PYSEC-2020-{n}", NOW - timedelta(days=400))
        for n in range(count - 1)
    }
    entries["GHSA-aaaa-bbbb-cccc.json"] = advisory("GHSA-aaaa-bbbb-cccc", newest)
    return entries


def test_a_fresh_complete_archive_is_accepted(tmp_path: Path) -> None:
    summary = vuln.validate_archive(archive(tmp_path, fresh(5)), NOW, 14, 5)
    assert summary.entries == 5
    assert summary.newest == NOW - timedelta(hours=3)


# --- the freshness limit (finding F-3 of H-V) -----------------------------------------------------
# Decision 0013 section 9 (OD-57 in .claude/docs/decisions/open-decisions.md): the newest entry of
# the advisory copy is at most 14 days old. The gate's own limit, not a number the test passes, is
# fed a copy just inside and just outside 14 days, so lengthening or shortening it fails these
# tests.


@pytest.mark.parametrize(
    ("age", "accepted"),
    [(timedelta(days=14, hours=-1), True), (timedelta(days=14, hours=1), False)],
)
def test_the_gates_limit_is_14_days(tmp_path: Path, age: timedelta, accepted: bool) -> None:
    copy = archive(tmp_path, fresh(5, newest=NOW - age))
    limit = vuln.freshness_limit({})
    if accepted:
        assert vuln.validate_archive(copy, NOW, limit, 5).newest == NOW - age
    else:
        with pytest.raises(vuln.VulnDbError, match="older than the limit of 14 days"):
            vuln.validate_archive(copy, NOW, limit, 5)


def test_a_run_without_an_override_uses_the_14_day_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """main passes the gate's limit to the run: a copy 15 days old fails it (exit 1)."""
    stale = archive(tmp_path, fresh(5, newest=NOW - timedelta(days=15)))
    root = tmp_path / "root"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "osv-scanner").write_text("")
    seen: list[int] = []

    def run(work: Path, max_age_days: int) -> int:
        seen.append(max_age_days)
        vuln.validate_archive(stale, NOW, max_age_days, 5)
        return 0

    monkeypatch.delenv("PY_VULN_MAX_AGE_DAYS", raising=False)
    monkeypatch.setattr(vuln, "ROOT", root)
    monkeypatch.setattr(vuln, "run", run)
    monkeypatch.chdir(root)
    assert vuln.main() == 1
    assert "older than the limit of 14 days" in capsys.readouterr().err
    assert seen == [vuln.MAX_AGE_DAYS]


@pytest.mark.parametrize(("value", "limit"), [("0", 0), ("7", 7), ("14", 14)])
def test_the_override_may_make_the_limit_stricter(value: str, limit: int) -> None:
    assert vuln.freshness_limit({"PY_VULN_MAX_AGE_DAYS": value}) == limit


@pytest.mark.parametrize("value", ["15", "14000", "-1", "fourteen", ""])
def test_the_override_may_not_weaken_the_limit(
    value: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(ValueError, match="PY_VULN_MAX_AGE_DAYS"):
        vuln.freshness_limit({"PY_VULN_MAX_AGE_DAYS": value})
    monkeypatch.setenv("PY_VULN_MAX_AGE_DAYS", value)
    monkeypatch.chdir(vuln.ROOT)
    assert vuln.main() == 2
    assert "py-vuln: PY_VULN_MAX_AGE_DAYS" in capsys.readouterr().err


def test_a_stale_archive_is_refused(tmp_path: Path) -> None:
    """Planted defect: the newest advisory is 15 days old."""
    stale = archive(tmp_path, fresh(5, newest=NOW - timedelta(days=15)))
    with pytest.raises(vuln.VulnDbError, match="older than the limit of 14 days"):
        vuln.validate_archive(stale, NOW, 14, 5)


def test_an_archive_from_the_future_is_refused(tmp_path: Path) -> None:
    ahead = archive(tmp_path, fresh(5, newest=NOW + timedelta(days=2)))
    with pytest.raises(vuln.VulnDbError, match="in the future"):
        vuln.validate_archive(ahead, NOW, 14, 5)


def test_too_few_advisories_are_refused(tmp_path: Path) -> None:
    with pytest.raises(vuln.VulnDbError, match="only 5 advisories, need at least 6"):
        vuln.validate_archive(archive(tmp_path, fresh(5)), NOW, 14, 6)


@pytest.mark.parametrize(
    ("entry", "content", "reason"),
    [
        ("../PYSEC-1.json", advisory("PYSEC-1", NOW), "unexpected entry"),
        ("sub/PYSEC-1.json", advisory("PYSEC-1", NOW), "unexpected entry"),
        ("PYSEC-1.txt", advisory("PYSEC-1", NOW), "unexpected entry"),
        ("PYSEC-1.json", advisory("PYSEC-2", NOW), "does not hold advisory PYSEC-1"),
        ("PYSEC-1.json", "{not json", "unreadable"),
        ("PYSEC-1.json", {"id": "PYSEC-1"}, "no modified date"),
        ("PYSEC-1.json", {"id": "PYSEC-1", "modified": "yesterday"}, "not a timestamp"),
        ("PYSEC-1.json", advisory("PYSEC-1", NOW, ecosystem="npm"), "not a PyPI advisory"),
    ],
)
def test_a_damaged_or_foreign_entry_is_refused(
    tmp_path: Path, entry: str, content: object, reason: str
) -> None:
    entries = fresh(3)
    entries[entry] = content
    with pytest.raises(vuln.VulnDbError, match=reason):
        vuln.validate_archive(archive(tmp_path, entries), NOW, 14, 3)


def test_a_file_that_is_not_a_zip_archive_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "all.zip"
    path.write_text("<html>error page</html>")
    with pytest.raises(vuln.VulnDbError, match="not a zip archive"):
        vuln.validate_archive(path, NOW, 14, 1)


def headers_for(body: bytes, md5: bytes | None = None, length: int | None = None) -> str:
    digest = base64.b64encode(md5 or hashlib.md5(body, usedforsecurity=False).digest()).decode()
    return (
        "HTTP/2 200\r\n"
        "x-goog-hash: crc32c=wKrOPQ==\r\n"
        f"x-goog-hash: md5={digest}\r\n"
        f"x-goog-stored-content-length: {len(body) if length is None else length}\r\n"
        "\r\n"
    )


def test_a_transfer_matching_the_published_md5_and_length_is_accepted(tmp_path: Path) -> None:
    body = tmp_path / "all.zip"
    body.write_bytes(b"archive bytes")
    vuln.check_transfer(headers_for(b"archive bytes"), body)


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        (headers_for(b"archive BYTES"), "md5 mismatch"),
        (headers_for(b"archive bytes", length=99), "length mismatch"),
        ("HTTP/2 200\r\nx-goog-stored-content-length: 13\r\n\r\n", "no md5"),
        ("HTTP/2 200\r\nx-goog-hash: md5=AAAA\r\n\r\n", "no stored length"),
    ],
)
def test_a_damaged_or_unverifiable_transfer_is_refused(
    tmp_path: Path, headers: str, reason: str
) -> None:
    body = tmp_path / "all.zip"
    body.write_bytes(b"archive bytes")
    with pytest.raises(vuln.VulnDbError, match=reason):
        vuln.check_transfer(headers, body)


def test_the_last_response_of_a_redirect_chain_is_the_one_checked(tmp_path: Path) -> None:
    body = tmp_path / "all.zip"
    body.write_bytes(b"archive bytes")
    first = "HTTP/1.1 302 Found\r\nx-goog-hash: md5=AAAA\r\nlocation: https://x\r\n\r\n"
    vuln.check_transfer(first + headers_for(b"archive bytes"), body)


LOCK = """\
version = 1
requires-python = ">=3.14"

[[package]]
name = "webblock-provisioning"
version = "0.0.0"
source = { editable = "." }

[[package]]
name = "fastapi"
version = "0.142.2"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "psycopg-binary"
version = "3.3.6"
source = { registry = "https://pypi.org/simple" }
"""


def test_the_locked_registry_packages_are_listed(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text(LOCK)
    assert vuln.lock_packages(lock) == [("fastapi", "0.142.2"), ("psycopg-binary", "3.3.6")]


def report(packages: list[tuple[str, str, list[str]]]) -> dict[str, object]:
    return {
        "results": [
            {
                "source": {"path": "/x/uv.lock", "type": "lockfile"},
                "packages": [
                    {
                        "package": {"name": n, "version": v, "ecosystem": "PyPI"},
                        "vulnerabilities": [{"id": i, "summary": f"{i} summary"} for i in ids],
                    }
                    for n, v, ids in packages
                ],
            }
        ]
    }


def test_findings_are_read_from_the_scan_report() -> None:
    scanned, findings = vuln.read_report(
        report([("fastapi", "0.142.2", []), ("jinja2", "2.10", ["PYSEC-2019-217", "GHSA-x"])])
    )
    assert scanned == {("fastapi", "0.142.2"), ("jinja2", "2.10")}
    assert [(f.package, f.version, f.ident) for f in findings] == [
        ("jinja2", "2.10", "PYSEC-2019-217"),
        ("jinja2", "2.10", "GHSA-x"),
    ]


def test_a_scan_that_missed_a_locked_package_is_refused() -> None:
    scanned, _ = vuln.read_report(report([("fastapi", "0.142.2", [])]))
    with pytest.raises(vuln.VulnDbError, match="did not scan 1 locked packages: psycopg-binary"):
        vuln.check_scanned(scanned, [("fastapi", "0.142.2"), ("psycopg-binary", "3.3.6")])


def test_an_advisory_that_also_affects_another_ecosystem_is_accepted(tmp_path: Path) -> None:
    """Found on the real download: GHSA advisories of Rust-backed packages name crates.io and
    PyPI. Such an entry belongs in the PyPI export; one naming no PyPI package does not."""
    entries = fresh(3)
    mixed = advisory("GHSA-2cv4-cqwr-gwf7", NOW - timedelta(days=30), ecosystem="crates.io")
    mixed["affected"] = [
        {"package": {"ecosystem": "crates.io", "name": "pyo3"}},
        {"package": {"ecosystem": "PyPI", "name": "cryptography"}},
    ]
    entries["GHSA-2cv4-cqwr-gwf7.json"] = mixed
    assert vuln.validate_archive(archive(tmp_path, entries), NOW, 14, 4).entries == 4


TODAY = NOW.date()

EXCEPTION = """
[[exception]]
package = "Certifi"
version = "2026.7.22"
vulnerability = "GHSA-6q48-6c7w-9gw2"
reason = "no fixed release is 14 days old yet"
reviewer = "lead"
expires = {expires}
follow_up = "upgrade on the eligible day"
"""


def exceptions(tmp_path: Path, expires: str = "2026-10-20") -> list[object]:
    path = tmp_path / "vuln-exceptions.toml"
    path.write_text(EXCEPTION.format(expires=expires))
    loaded: list[object] = vuln.load_exceptions(path)
    return loaded


def finding(ident: str = "GHSA-6q48-6c7w-9gw2", version: str = "2026.7.22") -> object:
    return vuln.Finding("certifi", version, ident, "emSign roots")


def test_no_exceptions_file_means_no_exceptions(tmp_path: Path) -> None:
    assert vuln.load_exceptions(tmp_path / "absent.toml") == []


def test_a_finding_under_a_current_exception_passes_and_is_still_printed(tmp_path: Path) -> None:
    failures, notes = vuln.evaluate([finding()], exceptions(tmp_path), TODAY)
    assert failures == []
    assert len(notes) == 1
    assert notes[0].startswith("ACCEPTED certifi 2026.7.22: GHSA-6q48-6c7w-9gw2")


@pytest.mark.parametrize(
    "found",
    [
        pytest.param(finding(ident="PYSEC-2026-4320"), id="another-advisory"),
        pytest.param(finding(version="2026.10.19"), id="another-version"),
    ],
)
def test_an_exception_covers_only_the_exact_package_version_and_advisory(
    tmp_path: Path, found: object
) -> None:
    failures, notes = vuln.evaluate([found], exceptions(tmp_path), TODAY)
    assert len(failures) == 1 and failures[0].startswith("FINDING certifi")
    assert notes == [
        "certifi 2026.7.22 GHSA-6q48-6c7w-9gw2: the exception matches no finding; remove it"
    ]


@pytest.mark.parametrize(
    ("expires", "message"),
    [
        ("2026-10-09", "the exception expired on 2026-10-09"),
        ("2026-11-10", "more than 30 days ahead"),
    ],
)
def test_an_expired_or_too_long_exception_fails_the_gate(
    tmp_path: Path, expires: str, message: str
) -> None:
    failures, _ = vuln.evaluate([finding()], exceptions(tmp_path, expires), TODAY)
    assert any(message in line for line in failures)


def test_an_exception_is_valid_through_its_expiry_day(tmp_path: Path) -> None:
    failures, _ = vuln.evaluate([finding()], exceptions(tmp_path, "2026-10-10"), TODAY)
    assert failures == []


@pytest.mark.parametrize(
    ("text", "message"),
    [
        pytest.param(
            EXCEPTION.replace('reviewer = "lead"', 'reviewer = " "'),
            "reviewer is missing",
            id="empty-field",
        ),
        pytest.param(
            EXCEPTION.replace('package = "Certifi"\n', ""), "package is missing", id="missing-field"
        ),
        pytest.param(EXCEPTION, "expires must be a date", id="quoted-date"),
        pytest.param("[[exception]\n", "not valid TOML", id="bad-toml"),
    ],
)
def test_a_malformed_exception_is_refused(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "vuln-exceptions.toml"
    if "{expires}" in text:
        text = text.format(
            expires='"2026-10-20"' if message.startswith("expires") else "2026-10-20"
        )
    path.write_text(text)
    with pytest.raises(vuln.VulnDbError, match=message):
        vuln.load_exceptions(path)


def test_two_exceptions_for_the_same_finding_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "vuln-exceptions.toml"
    one = EXCEPTION.format(expires="2026-10-20")
    path.write_text(one + "\n" + one.replace('"Certifi"', '"certifi"'))
    with pytest.raises(vuln.VulnDbError, match="duplicate exception for certifi"):
        vuln.load_exceptions(path)


def test_the_recorded_exceptions_are_well_formed() -> None:
    for item in vuln.load_exceptions(vuln.EXCEPTIONS):
        assert item.package == vuln.normalize(item.package)
