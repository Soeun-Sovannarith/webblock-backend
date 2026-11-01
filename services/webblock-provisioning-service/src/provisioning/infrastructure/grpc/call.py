"""The one unary gRPC call every Provisioning client makes (decision 0022 E7).

Every call has a deadline (2 s by default), runs in the span ``grpc <method>``, and retries only
``UNAVAILABLE``: once after each wait of ``retry_waits`` (100 ms, then 300 ms). Any other end,
including ``DEADLINE_EXCEEDED``, is final. Every non-OK end raises ``CallFailure``, with the
``ErrorInfo.reason`` of the status details when the server sent one. The caller maps the failure
(fail closed). Messages are generated into ``_gen`` and there are no ``_grpc`` stubs (E3), so the
method is called by its path.

The identity headers come from ``web-block-commons`` (FR-8, TR-30, decision 0024): ``open_channel``
is its internal channel, whose interceptor drops ``authorization`` and every header only the
library sets, then adds ``x-request-id``, ``traceparent`` (and ``tracestate``) and, for a call made
for a user, ``x-user-id``. ``unary`` binds the library's context for each call (``inner_call``):
the request ID, the trace context of the ``grpc <method>`` span (none when tracing is off) and the
``user_id`` it is given, never a user of an outer context. No token is ever sent.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Final
from uuid import UUID

import grpc
import grpc.aio
from google.protobuf.message import Message
from google.rpc import error_details_pb2, status_pb2
from webblock_commons.grpc import internal_channel

from provisioning.infrastructure.inner_call import bound_call
from provisioning.infrastructure.telemetry.tracing import grpc_span

_DETAILS_KEY: Final = "grpc-status-details-bin"
DEFAULT_DEADLINE: Final = timedelta(seconds=2)
DEFAULT_RETRY_WAITS: Final = (timedelta(milliseconds=100), timedelta(milliseconds=300))


@dataclass(frozen=True, slots=True)
class CallFailure(Exception):
    """A call that did not end OK: its status code and the ``ErrorInfo.reason`` if present."""

    code: grpc.StatusCode
    reason: str | None = None


def open_channel(target: str) -> grpc.aio.Channel:
    """A lazy plaintext channel to one of our services at ``host:port`` (the mesh adds mTLS), with
    the library's internal-call interceptor. It connects on the first call; the owner closes it
    with ``await channel.close()``."""
    return internal_channel(target)


def _error_reason(err: grpc.aio.AioRpcError) -> str | None:
    """``ErrorInfo.reason`` of the status details the server sent, or None. ``grpc_status``'s
    ``from_call`` is for the sync API (an aio call's trailing metadata is a coroutine), so the
    ``grpc-status-details-bin`` trailer is parsed here. Any trouble reading it gives None."""
    try:
        trailers = err.trailing_metadata()
        raw = next(
            (v for k, v in trailers or () if k == _DETAILS_KEY and isinstance(v, bytes)), None
        )
        if raw is None:
            return None
        status = status_pb2.Status.FromString(raw)
        for detail in status.details:
            info = error_details_pb2.ErrorInfo()
            if detail.Unpack(info):
                return info.reason or None
    except Exception:  # a malformed details blob must not hide the real failure
        return None
    return None


async def unary[M: Message](
    channel: grpc.aio.Channel,
    method: str,
    request: Message,
    response_type: type[M],
    *,
    request_id: str,
    user_id: UUID | None = None,
    deadline: timedelta = DEFAULT_DEADLINE,
    retry_waits: tuple[timedelta, ...] = DEFAULT_RETRY_WAITS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> M:
    """Call ``method`` with ``request``; the response, or ``CallFailure`` when the RPC ends
    non-OK. ``user_id`` is the user the call is made for (``x-user-id``); None sends none. The
    deadline applies to each attempt (a retry is a new call). Errors that are not an RPC end (a
    closed channel's ``UsageError``, a serialization error) are not caught here; the client maps
    them."""
    callable_ = channel.unary_unary(
        method,
        request_serializer=request.__class__.SerializeToString,
        response_deserializer=response_type.FromString,
    )
    with grpc_span(method):  # the span ``grpc <method>``; its traceparent goes in the metadata
        # The library's ``bound`` is a generator context manager, which a frozen dataclass
        # exception cannot pass through (it writes ``__traceback__``), so the failure is raised
        # after the context ends, still inside the span.
        with bound_call(request_id, user_id):
            outcome = await _attempts(callable_, request, deadline, retry_waits, sleep)
        if isinstance(outcome, CallFailure):
            raise outcome
        # grpc is untyped (Any); response_type.FromString made it, so it is an M.
        response: M = outcome
        return response


async def _attempts(
    callable_: grpc.aio.UnaryUnaryMultiCallable,
    request: Message,
    deadline: timedelta,
    retry_waits: tuple[timedelta, ...],
    sleep: Callable[[float], Awaitable[None]],
) -> Message | CallFailure:
    """The response, or the ``CallFailure`` of the last attempt (returned, not raised)."""
    attempt = 0
    while True:
        call = callable_(request, timeout=deadline.total_seconds())
        try:
            response: Message = await call
        except grpc.aio.AioRpcError as err:
            code = err.code()
            if code is grpc.StatusCode.UNAVAILABLE and attempt < len(retry_waits):
                await sleep(retry_waits[attempt].total_seconds())
                attempt += 1
                continue
            return CallFailure(code, _error_reason(err))
        return response
