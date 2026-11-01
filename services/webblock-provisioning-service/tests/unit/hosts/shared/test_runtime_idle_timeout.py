"""The hosts bound the server's idle-in-transaction timeout by DB_TX_TIMEOUT (task H5b, lead's
ruling): no transaction of ours may last longer than DB_TX_TIMEOUT, so a session idle inside a
transaction for that long belongs to a stalled process, and the client gives up at that bound
anyway. No new configuration key."""

from datetime import timedelta

from provisioning.hosts.shared.runtime import engine_settings_of
from provisioning.infrastructure.config import DatabaseConfig


def test_the_idle_in_transaction_timeout_is_the_transaction_timeout() -> None:
    database = DatabaseConfig(
        host="127.0.0.1",
        port=5432,
        name="provisioning",
        ssl_mode="disable",
        max_conns=8,
        connect_timeout=timedelta(seconds=5),
        tx_timeout=timedelta(seconds=30),
        statement_timeout=timedelta(seconds=10),
        lock_timeout=timedelta(seconds=5),
    )
    settings = engine_settings_of(database)
    assert settings.idle_in_transaction_timeout == timedelta(seconds=30)
    settings.validate()
