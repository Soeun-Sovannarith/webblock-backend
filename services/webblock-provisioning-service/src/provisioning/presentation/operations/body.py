"""Input decoding: strict JSON, the empty body of a
mutation, and the flat arguments of an MCP tool call."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from provisioning.core.application.shared.strict_json import DuplicateKeyError, loads_strict
from provisioning.presentation.operations.errors import FieldCode, FieldError, MalformedRequest
from provisioning.presentation.operations.operations import INPUT_LIMIT, Operation


def parse_json_strict(raw: bytes) -> Any:
    """Decode one JSON value: valid UTF-8, no duplicate key at any depth, no NaN or Infinity,
    nothing after the value but white space. ``MalformedRequest`` (with no cause) otherwise."""
    try:
        return loads_strict(raw)
    except DuplicateKeyError:
        raise MalformedRequest("duplicate field") from None
    except ValueError:
        raise MalformedRequest("invalid JSON") from None
    except RecursionError:
        raise MalformedRequest("nesting too deep") from None


def check_empty_body(body: bytes) -> None:
    """A mutation takes no body (decision 0006 section 2): absent, empty, or the empty JSON
    object with any white space. Anything else is ``MalformedRequest``."""
    if not body:
        return
    value = parse_json_strict(body)
    if not isinstance(value, dict) or value:
        raise MalformedRequest("a mutation takes no fields")


def decode_arguments(op: Operation, arguments: Mapping[str, Any] | None) -> dict[str, str]:
    """The arguments of a tool call against the operation's inputs, which equal the tool's input
    schema: keys among the inputs, string values, except ``limit``, an integer (not a boolean)
    passed on in decimal for the shared parse. Anything else is ``MalformedRequest``
    naming the field (decision 0023 G5): a known argument by its name, an unknown one as
    ``arguments`` (its name is never echoed); a missing input is left absent so that the
    invocation reports it as ``REQUIRED``. The SDK has already decoded the JSON; duplicate keys
    are refused on the raw body by the MCP endpoint."""
    if arguments is None:
        return {}
    decoded: dict[str, str] = {}
    for name, value in arguments.items():
        if name not in op.inputs:
            raise MalformedRequest("unknown field", errors=_ARGUMENTS)
        if name == INPUT_LIMIT:
            if not isinstance(value, int) or isinstance(value, bool):
                raise MalformedRequest("field is not an integer", errors=_bad(name))
            decoded[name] = str(value)
            continue
        if not isinstance(value, str):
            raise MalformedRequest("field is not a string", errors=_bad(name))
        decoded[name] = value
    return decoded


_ARGUMENTS: Final = (FieldError("arguments", FieldCode.INVALID_FORMAT),)


def _bad(name: str) -> tuple[FieldError, ...]:
    return (FieldError(name, FieldCode.INVALID_FORMAT),)
