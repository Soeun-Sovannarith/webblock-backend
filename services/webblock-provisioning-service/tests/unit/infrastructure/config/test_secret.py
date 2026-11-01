"""The redacting Secret: it prints as the marker through every formatting path, and only ``reveal``
returns the value."""

from __future__ import annotations

import copy
import io
import json
import logging
import pickle

import pytest

from provisioning.infrastructure.config import REDACTED, Secret

VALUE = "s3cr3t-MARKER-91c2"


def test_every_formatting_path_prints_the_marker() -> None:
    s = Secret(VALUE)
    outputs = [
        str(s),
        repr(s),
        f"{s}",
        f"{s!r}",
        f"{s!s}",
        f"{s:>40}",
        "%s" % s,  # noqa: UP031  # the %-operator is one of the formatting paths under test
        "%r" % s,  # noqa: UP031  # the %-operator is one of the formatting paths under test
        "{}".format(s),  # noqa: UP032  # str.format is one of the formatting paths under test
        format(s, ""),
        repr([s]),
        repr({"k": s}),
        json.dumps({"k": s}, default=str),
    ]
    for out in outputs:
        assert VALUE not in out
        assert REDACTED in out


def test_logging_prints_the_marker() -> None:
    stream = io.StringIO()
    logger = logging.getLogger("test_secret_logging")
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    try:
        logger.warning("value %s and %r", Secret(VALUE), Secret(VALUE))
    finally:
        logger.removeHandler(handler)
    assert VALUE not in stream.getvalue()
    assert REDACTED in stream.getvalue()


def test_reveal_and_presence() -> None:
    assert Secret(VALUE).reveal() == VALUE
    assert Secret(VALUE).is_set()
    assert not Secret("").is_set()
    # A set and an unset secret print alike.
    assert str(Secret("")) == str(Secret(VALUE))


def test_a_secret_cannot_be_pickled_or_compared_by_value() -> None:
    with pytest.raises(TypeError):
        pickle.dumps(Secret(VALUE))
    # Equality by value would let a caller probe the secret through ==.
    assert Secret(VALUE) != Secret(VALUE)
    with pytest.raises(TypeError):
        hash(Secret(VALUE))


def test_copies_keep_the_value_hidden() -> None:
    s = Secret(VALUE)
    assert copy.copy(s).reveal() == VALUE
    assert copy.deepcopy(s).reveal() == VALUE
    assert VALUE not in repr(copy.deepcopy(s))


def test_no_attribute_holds_the_value_in_a_dict() -> None:
    # vars() and __dict__ are how generic dumpers reach fields; a Secret has neither.
    s = Secret(VALUE)
    assert not hasattr(s, "__dict__")
