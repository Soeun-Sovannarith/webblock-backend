"""The operator CLI's entry point: a missing or invalid setting names every refused key, never a
value, and exits 2 before any connection; a usage error exits 2 before the environment is read."""

import pytest

from provisioning.hosts.operator.__main__ import main
from provisioning.infrastructure.config import operator_keys


def test_an_empty_environment_names_every_key_and_exits_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["list"], environ={}) == 2
    err = capsys.readouterr().err
    for key in operator_keys():
        assert key in err
    assert "PG_PASS" in err


def test_a_password_is_never_printed(capsys: pytest.CaptureFixture[str]) -> None:
    secret = "operator-secret-value-0001"  # noqa: S105 - a marker, not a credential
    assert main(["list"], environ={"PG_PASS": secret, "PG_PORT": "not-a-port"}) == 2
    captured = capsys.readouterr()
    assert secret not in captured.err + captured.out


def test_a_usage_error_exits_2_without_reading_the_environment() -> None:
    with pytest.raises(SystemExit) as caught:
        main(["redrive"], environ={})
    assert caught.value.code == 2
