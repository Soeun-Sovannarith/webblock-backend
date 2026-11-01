"""The platform's trust roots (finding F-5 of H-V; decision 0024 G4a): the one TLS context the key
set fetch and the Project service's HTTP client use."""

from __future__ import annotations

import ssl
from pathlib import Path
from typing import cast

import pytest
from tests.unit.infrastructure.identity.issuer import local_ca, trusts_test_ca

from provisioning.infrastructure.tls import platform_roots


def test_tr26_the_platform_roots_ignore_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TR-26: OpenSSL's compiled-in CA file and directory, loaded by name, so SSL_CERT_FILE
    and SSL_CERT_DIR change nothing; verification on, TLS 1.2 or later, and no key log file from
    SSLKEYLOGFILE."""
    ca_file = tmp_path / "test-ca.pem"
    ca_file.write_bytes(local_ca().ca_pem)
    monkeypatch.setenv("SSL_CERT_FILE", str(ca_file))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path))
    monkeypatch.setenv("SSLKEYLOGFILE", str(tmp_path / "keys.log"))
    ctx = platform_roots()
    assert not trusts_test_ca(ctx)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2
    assert cast(object, ctx.keylog_filename) is None  # typeshed says str; None is off
    paths = ssl.get_default_verify_paths()
    if Path(paths.openssl_cafile).is_file():
        assert ctx.cert_store_stats()["x509_ca"] > 0


def test_tr26_a_platform_without_a_trust_store_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """TR-26: no compiled-in CA file or directory is an error, rather than a client that
    trusts nothing."""
    missing = ssl.DefaultVerifyPaths(
        "/nonexistent/cert.pem",
        "/nonexistent/certs",
        "SSL_CERT_FILE",
        "/nonexistent/cert.pem",
        "SSL_CERT_DIR",
        "/nonexistent/certs",
    )
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: missing)
    with pytest.raises(ValueError, match="tls: the platform has no certificate roots"):
        platform_roots()
