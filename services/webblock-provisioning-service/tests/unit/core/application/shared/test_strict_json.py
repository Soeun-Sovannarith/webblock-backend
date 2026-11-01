"""TR-2 and FR-7: the one strict JSON decoder that request bodies and configuration files share."""

from __future__ import annotations

import pytest

from provisioning.core.application.shared.strict_json import DuplicateKeyError, loads_strict


@pytest.mark.parametrize(
    "raw",
    [b'{"a":1,"a":2}', b'{"a":{"b":1,"b":2}}', b'[{"a":1,"a":1}]'],
)
def test_tr2_strict_json_refuses_duplicate_keys_at_any_depth(raw: bytes) -> None:
    with pytest.raises(DuplicateKeyError):
        loads_strict(raw)


@pytest.mark.parametrize(
    "raw",
    [b"\xff\xfe", b'{"a":NaN}', b'{"a":Infinity}', b'{"a":-Infinity}', b"{} trailing", b""],
)
def test_tr2_strict_json_refuses_bad_utf8_constants_and_trailing_data(raw: bytes) -> None:
    with pytest.raises(ValueError):
        loads_strict(raw)


def test_tr2_strict_json_refuses_too_deep_nesting() -> None:
    with pytest.raises(RecursionError):
        loads_strict(b"[" * 100_000 + b"]" * 100_000)


def test_tr2_strict_json_decodes_nested_values_and_white_space() -> None:
    assert loads_strict(b' {"a":[1,{"b":"c"}],"d":null}\n') == {"a": [1, {"b": "c"}], "d": None}
