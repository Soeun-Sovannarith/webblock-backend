"""AMQP header tables as aio-pika takes them."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Final

from pamqp.common import FieldTable

# The types of pamqp's ``FieldValue`` other than None; a list or a dict holds field values too.
_FIELD_TYPES: Final = (bool, bytes, bytearray, Decimal, list, dict, float, int, str, datetime)


def field_table(headers: Mapping[str, object]) -> FieldTable:
    """A copy of ``headers`` as an AMQP field table. Every value is a field value already:
    decoded from a broker message, or a text the trace propagator set; any other value is a
    programming error (``TypeError``), never converted."""
    table: FieldTable = {}
    for key, value in headers.items():
        if value is not None and not isinstance(value, _FIELD_TYPES):
            raise TypeError(f"header {key} holds no AMQP field value")
        table[key] = value
    return table
