"""The platform's trust roots (decision 0006 section 5, decision 0024 G4; finding F-5 of H-V): the
one TLS client context for the calls whose trust must not come from the process environment.
"""

from __future__ import annotations

import ssl
from pathlib import Path


def platform_roots() -> ssl.SSLContext:
    """The TLS client context of the key set fetch (FR-1, passed to web-block-commons) and of the
    Project service's HTTP client (FR-5): the platform's certificate roots only, as OpenSSL was
    built to find them (``openssl_cafile`` and ``openssl_capath`` of
    ``ssl.get_default_verify_paths()``), with certificate and host name verification and TLS 1.2 or
    later (decision 0006 section 5; finding F-5 of H-V).

    ``ssl.create_default_context()`` without a CA would call ``load_default_certs``, which lets
    ``SSL_CERT_FILE`` and ``SSL_CERT_DIR`` replace those roots; naming the CA file and directory
    explicitly skips that. The context's ``SSLKEYLOGFILE`` key log is switched off too, so nothing
    in the process environment widens trust or writes TLS secrets. ``ValueError`` when neither the
    CA file nor the directory exists: a client that trusts nothing would fail every call, and a
    verifier would answer 503 forever."""
    paths = ssl.get_default_verify_paths()
    cafile = paths.openssl_cafile if Path(paths.openssl_cafile).is_file() else None
    capath = paths.openssl_capath if Path(paths.openssl_capath).is_dir() else None
    if cafile is None and capath is None:
        raise ValueError(
            "tls: the platform has no certificate roots (OpenSSL's default CA file "
            "and directory are both missing)"
        )
    ctx = ssl.create_default_context(cafile=cafile, capath=capath)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    # typeshed types keylog_filename as str; None, its default, turns off the SSLKEYLOGFILE key log.
    ctx.keylog_filename = None  # type: ignore[assignment]  # None is valid at run time
    return ctx
