"""The one conversion of AMQP headers into the field table aio-pika takes (TR-22, TR-23)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from provisioning.infrastructure.rabbitmq.headers import field_table


def test_tr22_field_values_are_kept_in_a_copy() -> None:
    """TR-22: every AMQP field value passes unchanged, and the table is a new dict."""
    headers: dict[str, object] = {
        "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
        "x-death": [{"count": 2, "queue": "q", "time": datetime(2026, 10, 8, tzinfo=UTC)}],
        "x-delivery-count": 3,
        "flag": True,
        "raw": b"\x00",
        "amount": Decimal("1.5"),
        "none": None,
    }
    table = field_table(headers)
    assert table == headers
    assert table is not headers


def test_tr23_a_value_that_is_no_field_value_is_refused() -> None:
    """TR-23: a header value AMQP cannot carry is a ``TypeError``, never converted to text."""
    with pytest.raises(TypeError):
        field_table({"bad": object()})
