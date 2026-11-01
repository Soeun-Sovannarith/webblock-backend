"""py-vuln: osv-scanner offline on uv.lock against one verified OSV PyPI download per run (OD-57).

Run by ``make py-vuln`` as ``python3.14 -I scripts/py/vuln.py`` from the module root, after
``scripts/py/tools.sh osv-scanner`` has verified bin/osv-scanner against its pinned checksum. It
uses the standard library only, and follows the discipline of the Go reference's gate
(scripts/vulndb-fetch.sh, now only on branch go-reference):

1. One download of https://osv-vulnerabilities.storage.googleapis.com/PyPI/all.zip into a fresh
   temporary directory under bin/, HTTPS only (TLS 1.2 or later, redirects too), bounded in time.
   An earlier copy is never reused.
2. The transfer is checked against the md5 and the stored length that Google Cloud Storage sends in
   the x-goog-hash and x-goog-stored-content-length response headers. OSV publishes no checksum
   file and no signature, so this detects a damaged or truncated transfer, not a malicious server;
   authenticity rests on TLS to osv-vulnerabilities.storage.googleapis.com.
3. The archive itself: every entry is ``<ID>.json`` at the top level (no directory, no traversal),
   holds the advisory of that id with a ``modified`` timestamp, and affects PyPI packages only; at
   least MIN_ADVISORIES entries; and the newest ``modified`` is at most MAX_AGE_DAYS (14) old and
   not in the future. A stale copy is refused: the gate fails closed (OD-57).
4. A canary: osv-scanner must find PYSEC-2018-28 in a lock file pinning requests 2.19.0 against the
   same copy, so a copy it cannot read, or one it reads as empty, fails the gate.
5. The scan of uv.lock: ``osv-scanner scan --offline --offline-vulnerabilities`` with HOME,
   XDG_CACHE_HOME and OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY inside the temporary directory, so it
   reads only the verified copy and no network. Every registry package of uv.lock must appear in
   the report as scanned. The copy's SHA-256 is compared before and after, so the scanner cannot
   have replaced it.
6. Any finding fails the gate and is listed, unless scripts/py/vuln-exceptions.toml holds an
   exception naming it exactly (package, version, advisory id) with a reason, a reviewer, an expiry
   date (valid through that day, at most MAX_EXCEPTION_DAYS ahead) and a follow-up (CLAUDE.md
   section 4). An accepted finding is still printed. An expired exception, or one that expires too
   far ahead, fails the gate whether or not it still matches; one that matches nothing is reported
   for removal. The version is part of the match, so an upgrade ends the exception.

Environment, for testing the failure path only: PY_VULN_MAX_AGE_DAYS (default 14), a whole number
from 0 to 14; a larger value would weaken the gate and is refused as a usage error. Never reads
.env. Exit status 0 when every finding is under a current exception (or there is none), 1 on any
other finding, a bad exception or a refused download, 2 on a usage or tool error.
"""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OSV_URL = "https://osv-vulnerabilities.storage.googleapis.com/PyPI/all.zip"
EXCEPTIONS = ROOT / "scripts" / "py" / "vuln-exceptions.toml"
MAX_AGE_DAYS = 14
MAX_EXCEPTION_DAYS = 30
MIN_ADVISORIES = 10000
CLOCK_SKEW = timedelta(days=1)
DOWNLOAD_SECONDS = 600
SCAN_SECONDS = 300
ENTRY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.json$")
CANARY_LOCK = """\
version = 1
requires-python = ">=3.8"

[[package]]
name = "requests"
version = "2.19.0"
source = { registry = "https://pypi.org/simple" }
"""
CANARY_ID = "PYSEC-2018-28"


class VulnDbError(Exception):
    pass


@dataclass(frozen=True)
class Summary:
    entries: int
    newest: datetime


@dataclass(frozen=True)
class Finding:
    package: str
    version: str
    ident: str
    summary: str


@dataclass(frozen=True)
class Accepted:
    package: str
    version: str
    ident: str
    reason: str
    reviewer: str
    expires: date
    follow_up: str


def parse_time(value: str) -> datetime:
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


def validate_archive(path: Path, now: datetime, max_age_days: int, min_entries: int) -> Summary:
    """Check the OSV archive at path; return its size and newest advisory, or raise VulnDbError."""
    if not zipfile.is_zipfile(path):
        raise VulnDbError(f"{path.name} is not a zip archive")
    newest: datetime | None = None
    count = 0
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            name = info.filename
            if info.is_dir() or not ENTRY.match(name):
                raise VulnDbError(f"unexpected entry in the archive: {name!r}")
            ident = name.removesuffix(".json")
            try:
                entry = json.loads(archive.read(info))
            except ValueError:
                raise VulnDbError(f"{name} is unreadable") from None
            if not isinstance(entry, dict) or entry.get("id") != ident:
                raise VulnDbError(f"{name} does not hold advisory {ident}")
            modified = entry.get("modified")
            if not isinstance(modified, str):
                raise VulnDbError(f"{name} has no modified date")
            try:
                when = parse_time(modified)
            except ValueError:
                raise VulnDbError(f"{name}: modified {modified!r} is not a timestamp") from None
            # An advisory may also name other ecosystems (a Rust crate behind a PyPI package),
            # but one that names packages must name at least one on PyPI.
            ecosystems = {
                str((affected.get("package") or {}).get("ecosystem", ""))
                for affected in entry.get("affected") or []
            }
            if ecosystems and not any(e.startswith("PyPI") for e in ecosystems):
                raise VulnDbError(
                    f"{name} is not a PyPI advisory (ecosystems {', '.join(sorted(ecosystems))})"
                )
            newest = when if newest is None or when > newest else newest
            count += 1
    if count < min_entries or newest is None:
        raise VulnDbError(f"only {count} advisories, need at least {min_entries}")
    if newest > now + CLOCK_SKEW:
        raise VulnDbError(f"the newest advisory, modified {newest.isoformat()}, is in the future")
    if now - newest > timedelta(days=max_age_days):
        raise VulnDbError(
            f"the newest advisory was modified {newest.isoformat()}, older than the limit of "
            f"{max_age_days} days"
        )
    return Summary(count, newest)


def last_response(headers: str) -> list[tuple[str, str]]:
    """The header lines of the last response in a curl -D dump (after any redirect)."""
    blocks = [b for b in re.split(r"\r?\n\r?\n", headers) if b.strip()]
    if not blocks:
        return []
    pairs = []
    for line in blocks[-1].splitlines()[1:]:
        key, sep, value = line.partition(":")
        if sep:
            pairs.append((key.strip().lower(), value.strip()))
    return pairs


def check_transfer(headers: str, body: Path) -> None:
    """Compare the downloaded body with the md5 and stored length the server sent."""
    pairs = last_response(headers)
    md5s = [
        m.group(1)
        for k, v in pairs
        if k == "x-goog-hash"
        for m in [re.search(r"md5=([A-Za-z0-9+/=]+)", v)]
        if m
    ]
    lengths = [v for k, v in pairs if k == "x-goog-stored-content-length"]
    if not md5s:
        raise VulnDbError("no md5 in the response headers; the transfer cannot be verified")
    if not lengths:
        raise VulnDbError("no stored length in the response headers")
    size = body.stat().st_size
    if str(size) != lengths[-1]:
        raise VulnDbError(f"length mismatch: got {size} bytes, the server stored {lengths[-1]}")
    got = base64.b64encode(hashlib.md5(body.read_bytes(), usedforsecurity=False).digest()).decode()
    if got != md5s[-1]:
        raise VulnDbError(f"md5 mismatch: got {got}, the server sent {md5s[-1]}")


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def lock_packages(lock: Path) -> list[tuple[str, str]]:
    """The (name, version) of every package uv.lock takes from a registry, in lock order."""
    data = tomllib.loads(lock.read_text())
    return [
        (p["name"], p["version"])
        for p in data.get("package", [])
        if isinstance(p.get("source"), dict) and "registry" in p["source"]
    ]


def read_report(report: dict[str, object]) -> tuple[set[tuple[str, str]], list[Finding]]:
    """The packages osv-scanner scanned and the vulnerabilities it reported for them."""
    scanned: set[tuple[str, str]] = set()
    findings: list[Finding] = []
    results = report.get("results") or []
    if not isinstance(results, list):
        raise VulnDbError("osv-scanner's report has no list of results")
    for result in results:
        for entry in result.get("packages") or []:
            package = entry["package"]
            name, version = str(package["name"]), str(package["version"])
            scanned.add((name, version))
            for vuln in entry.get("vulnerabilities") or []:
                summary = str(vuln.get("summary") or "").strip()
                findings.append(Finding(name, version, str(vuln["id"]), summary))
    return scanned, findings


def check_scanned(scanned: set[tuple[str, str]], locked: list[tuple[str, str]]) -> None:
    seen = {(normalize(n), v) for n, v in scanned}
    missing = [n for n, v in locked if (normalize(n), v) not in seen]
    if missing:
        raise VulnDbError(
            f"osv-scanner did not scan {len(missing)} locked packages: {', '.join(missing)}"
        )


def load_exceptions(path: Path) -> list[Accepted]:
    """The exceptions of path (none when the file is absent), or VulnDbError on a malformed one."""
    if not path.is_file():
        return []
    try:
        entries = tomllib.loads(path.read_text()).get("exception", [])
    except tomllib.TOMLDecodeError as error:
        raise VulnDbError(f"{path.name} is not valid TOML: {error}") from None
    if not isinstance(entries, list):
        raise VulnDbError(f"{path.name}: exception must be an array of tables")
    accepted = []
    seen: set[tuple[str, str, str]] = set()
    for n, entry in enumerate(entries, start=1):
        where = f"{path.name} exception {n}"
        if not isinstance(entry, dict):
            raise VulnDbError(f"{where} is not a table")
        for field in ("package", "version", "vulnerability", "reason", "reviewer", "follow_up"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise VulnDbError(f"{where}: {field} is missing or empty")
        expires = entry.get("expires")
        if not isinstance(expires, date) or isinstance(expires, datetime):
            raise VulnDbError(f"{where}: expires must be a date (YYYY-MM-DD, unquoted)")
        key = (normalize(entry["package"]), entry["version"], entry["vulnerability"])
        if key in seen:
            raise VulnDbError(
                f"{where}: duplicate exception for {key[0]} {key[1]} {key[2]}; keep one entry"
            )
        seen.add(key)
        accepted.append(
            Accepted(
                normalize(entry["package"]),
                entry["version"],
                entry["vulnerability"],
                entry["reason"],
                entry["reviewer"],
                expires,
                entry["follow_up"],
            )
        )
    return accepted


def evaluate(
    findings: list[Finding], accepted: list[Accepted], today: date
) -> tuple[list[str], list[str]]:
    """The failures and the accepted or obsolete items, as lines to print."""
    failures: list[str] = []
    notes: list[str] = []
    by_key = {(a.package, a.version, a.ident): a for a in accepted}
    used: set[tuple[str, str, str]] = set()
    for item in accepted:
        label = f"{item.package} {item.version} {item.ident}"
        if item.expires < today:
            failures.append(f"{label}: the exception expired on {item.expires.isoformat()}")
        elif item.expires > today + timedelta(days=MAX_EXCEPTION_DAYS):
            failures.append(
                f"{label}: the exception expires {item.expires.isoformat()}, more than "
                f"{MAX_EXCEPTION_DAYS} days ahead"
            )
    for f in findings:
        label = f"{f.package} {f.version}: {f.ident} {f.summary}"
        key = (normalize(f.package), f.version, f.ident)
        granted = by_key.get(key)
        if granted is None:
            failures.append(f"FINDING {label}")
            continue
        used.add(key)
        if today <= granted.expires <= today + timedelta(days=MAX_EXCEPTION_DAYS):
            notes.append(
                f"ACCEPTED {label}: until {granted.expires.isoformat()} by {granted.reviewer} "
                f"(follow-up: {granted.follow_up})"
            )
    for key, item in by_key.items():
        if key not in used and item.expires >= today:
            notes.append(
                f"{item.package} {item.version} {item.ident}: the exception matches no finding; "
                "remove it"
            )
    return failures, notes


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(work: Path) -> Path:
    body, headers = work / "all.zip", work / "headers.txt"
    command = [
        "curl", "-sS", "--fail", "--proto", "=https", "--proto-redir", "=https", "--tlsv1.2",
        "--connect-timeout", "20", "--max-time", "300", "--retry", "3", "--retry-delay", "5",
        "--retry-max-time", str(DOWNLOAD_SECONDS), "--retry-all-errors",
        "-D", str(headers), "-o", str(body), OSV_URL,
    ]  # fmt: skip
    print(f"py-vuln: downloading {OSV_URL}", flush=True)
    result = subprocess.run(command, check=False, timeout=DOWNLOAD_SECONDS + 60)  # noqa: S603 - fixed argv
    if result.returncode != 0:
        raise VulnDbError(f"download failed (curl exit {result.returncode})")
    check_transfer(headers.read_text(errors="replace"), body)
    return body


def scan(scanner: Path, lock: Path, cache: Path, work: Path) -> tuple[int, dict[str, object]]:
    """Run osv-scanner offline on lock with the verified copy under cache; return its exit
    status and JSON report."""
    out = work / f"report.{lock.parent.name}.json"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(work / "home"),
        "XDG_CACHE_HOME": str(work / "home" / ".cache"),
        "XDG_CONFIG_HOME": str(work / "home" / ".config"),
        "OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY": str(cache),
    }
    command = [
        str(scanner), "scan", "--offline", "--offline-vulnerabilities", "--format", "json",
        "--all-packages", "--output-file", str(out), "-L", str(lock),
    ]  # fmt: skip
    with (work / "scanner.log").open("a") as log:
        result = subprocess.run(  # noqa: S603 - fixed argv
            command, env=env, stdout=log, stderr=log, check=False, timeout=SCAN_SECONDS
        )
    if result.returncode not in (0, 1):
        raise VulnDbError(
            f"osv-scanner exited {result.returncode} on {lock.name}; see its log above"
        )
    try:
        report = json.loads(out.read_text())
    except OSError, ValueError:
        raise VulnDbError(f"osv-scanner wrote no readable report for {lock}") from None
    if not isinstance(report, dict):
        raise VulnDbError(f"osv-scanner's report for {lock} is not an object")
    return result.returncode, report


def run(work: Path, max_age_days: int) -> int:
    scanner = ROOT / "bin" / "osv-scanner"
    lock = ROOT / "uv.lock"
    accepted = load_exceptions(EXCEPTIONS)
    body = download(work)
    summary = validate_archive(body, datetime.now(UTC), max_age_days, MIN_ADVISORIES)
    print(
        f"py-vuln: verified copy: {summary.entries} PyPI advisories, newest modified "
        f"{summary.newest.isoformat()} (limit {max_age_days} days), sha256 {sha256(body)}"
    )
    cache = work / "db"
    (cache / "osv-scalibr" / "PyPI").mkdir(parents=True)
    copy = cache / "osv-scalibr" / "PyPI" / "all.zip"
    shutil.move(body, copy)
    before = sha256(copy)

    canary_dir = work / "canary"
    canary_dir.mkdir()
    (canary_dir / "uv.lock").write_text(CANARY_LOCK)
    status, report = scan(scanner, canary_dir / "uv.lock", cache, work)
    _, canary_findings = read_report(report)
    if status != 1 or CANARY_ID not in {f.ident for f in canary_findings}:
        raise VulnDbError(
            f"canary: osv-scanner did not report {CANARY_ID} for requests 2.19.0 against the "
            "verified copy, so the copy is not being read"
        )
    print(f"py-vuln: canary: {CANARY_ID} found in requests 2.19.0 (the copy is read)")

    status, report = scan(scanner, lock, cache, work)
    scanned, findings = read_report(report)
    locked = lock_packages(lock)
    check_scanned(scanned, locked)
    if sha256(copy) != before:
        raise VulnDbError("the advisory copy changed during the scan")
    if status == 1 and not findings:
        raise VulnDbError("osv-scanner exited 1 but its report lists no finding")
    print(f"py-vuln: uv.lock: {len(locked)} registry packages scanned offline")
    failures, notes = evaluate(findings, accepted, datetime.now(UTC).date())
    for line in notes:
        print(f"py-vuln: {line}")
    if not failures:
        print(
            "py-vuln: no known vulnerability"
            if not findings
            else f"py-vuln: {len(findings)} findings, each under a current exception"
        )
        return 0
    for line in failures:
        print(f"py-vuln: {line}")
    print(f"py-vuln: FAILED: {len(failures)} problems", file=sys.stderr)
    return 1


def freshness_limit(environ: Mapping[str, str]) -> int:
    """The advisory freshness limit in days: MAX_AGE_DAYS, or PY_VULN_MAX_AGE_DAYS when it is set
    to a stricter value (to show the failure path). ValueError for anything else."""
    value = environ.get("PY_VULN_MAX_AGE_DAYS")
    if value is None:
        return MAX_AGE_DAYS
    try:
        days = int(value)
    except ValueError:
        raise ValueError("PY_VULN_MAX_AGE_DAYS is not a whole number") from None
    if not 0 <= days <= MAX_AGE_DAYS:
        raise ValueError(
            f"PY_VULN_MAX_AGE_DAYS must be from 0 to {MAX_AGE_DAYS}; it may only make the gate "
            "stricter"
        )
    return days


def main() -> int:
    if Path.cwd().resolve() != ROOT:
        print(f"py-vuln: run from the module root {ROOT}", file=sys.stderr)
        return 2
    try:
        max_age_days = freshness_limit(os.environ)
    except ValueError as error:
        print(f"py-vuln: {error}", file=sys.stderr)
        return 2
    if not (ROOT / "bin" / "osv-scanner").is_file():
        print("py-vuln: bin/osv-scanner is missing; run make py-tools", file=sys.stderr)
        return 2
    (ROOT / "bin").mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".py-vuln.", dir=ROOT / "bin"))
    try:
        return run(work, max_age_days)
    except VulnDbError as error:
        log = work / "scanner.log"
        if log.is_file():
            sys.stderr.write(log.read_text(errors="replace"))
        print(f"py-vuln: FAILED: {error}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
