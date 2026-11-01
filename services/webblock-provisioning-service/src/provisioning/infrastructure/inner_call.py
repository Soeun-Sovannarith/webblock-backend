"""The context of one call to our own services (FR-8, TR-30, decision 0024 G3).

web-block-commons' internal clients (the gRPC ``internal_channel`` and the HTTP
``InternalTransport``) drop ``authorization`` and every header only the library may set, then add
``x-request-id``, ``traceparent`` (and ``tracestate``) and ``x-user-id`` from the library's current
context. ``bound_call`` makes that context the one of this call: the request ID, the trace context
of the current span (none when tracing is off, decision 0022 E9), and the user the call is made
for, never a user of an outer context. No token is ever part of it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from webblock_commons.core import context as commons_context
from webblock_commons.core.traceparent import parse_traceparent
from webblock_commons.core.user import WebBlockUser

from provisioning.infrastructure.telemetry.tracing import inject_metadata


def call_context(request_id: str, user_id: UUID | None) -> commons_context.RequestContext:
    """The library's context of one call: ``request_id``, the ``traceparent`` and ``tracestate``
    of the current span, and the user ``user_id`` names, if any."""
    trace = dict(inject_metadata())
    parent = parse_traceparent(trace.get("traceparent", ""))
    return commons_context.RequestContext(
        request_id=request_id,
        traceparent=parent,
        tracestate=trace.get("tracestate") if parent is not None else None,
        user=WebBlockUser(user_id=user_id) if user_id is not None else None,
    )


@contextmanager
def bound_call(request_id: str, user_id: UUID | None) -> Iterator[None]:
    """The block runs with ``call_context(request_id, user_id)`` as the library's context; the
    outer context comes back after it. A frozen dataclass exception cannot pass through this
    generator (it writes ``__traceback__``): raise such an error after the block."""
    with commons_context.bound(call_context(request_id, user_id)):
        yield
