"""FR-54 (TR-53): the lock that makes hosting row inserts of one sub-domain run one at a time.

``pg_advisory_xact_lock(class, key)``, the two-integer form, so its key space never meets the
one-bigint keys of the bootstrap's session lock. The key is a stable hash of the sub-domain: the
same in every process and every Python run (no ``hash()``, which is salted per process)."""

from __future__ import annotations

import hashlib

from provisioning.infrastructure.postgres.hosting_insert import (
    SUBDOMAIN_LOCK_CLASS,
    subdomain_lock_key,
)

INT4_MIN, INT4_MAX = -(2**31), 2**31 - 1


def test_fr54_the_lock_key_is_a_stable_hash_of_the_subdomain() -> None:
    expected = int.from_bytes(hashlib.sha256(b"victim-shop").digest()[:4], "big", signed=True)
    assert subdomain_lock_key("victim-shop") == expected
    assert subdomain_lock_key("victim-shop") == subdomain_lock_key("victim-shop")


def test_fr54_other_subdomains_take_other_keys() -> None:
    keys = {subdomain_lock_key(f"site-{n}") for n in range(1000)}
    assert len(keys) == 1000


def test_fr54_class_and_key_fit_the_two_integer_lock() -> None:
    assert INT4_MIN <= SUBDOMAIN_LOCK_CLASS <= INT4_MAX
    for name in ("a", "victim-shop", "x" * 63):
        assert INT4_MIN <= subdomain_lock_key(name) <= INT4_MAX
