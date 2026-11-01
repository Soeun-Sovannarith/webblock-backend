"""Argument types shared by the operator CLI's commands; each refusal is a usage error (exit 2)
whose text never quotes the value."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from uuid import UUID

from provisioning.core.domain import DomainError, parse_id, validate_requester


def project_uuid(text: str) -> UUID:
    """A canonical lowercase UUID, not nil."""
    try:
        return parse_id(text)
    except DomainError:
        raise argparse.ArgumentTypeError("must be a canonical lowercase UUID, not nil") from None


def operator_name(text: str) -> str:
    """The operator's name for ``--by``: the requester rule."""
    try:
        validate_requester(text)
    except DomainError:
        raise argparse.ArgumentTypeError(
            "must be 1 to 255 bytes without control characters or surrounding spaces"
        ) from None
    return text


def bounded(low: int, high: int) -> Callable[[str], int]:
    """A whole number from ``low`` to ``high``."""

    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError("must be a whole number") from None
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"must be {low} to {high}")
        return value

    return parse
