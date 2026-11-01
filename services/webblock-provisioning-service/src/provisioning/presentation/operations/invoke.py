"""The one path of both transports (decision 0006 section
1): parse the inputs through the domain, build the operation's command or query and send it
through the application's buses under the request timeout (decision 0025 CQ11), and turn the
result or the error into the contract's body, with one log record per call.

The HTTP and MCP packages only adapt their protocol to ``Invoker``; neither contains workflow logic.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol
from uuid import UUID

from provisioning.core.application import (
    MAX_CORRELATION_ID_LEN,
    Action,
    AdminListCommandsQuery,
    AuditActorKind,
    BuildReleaseCommand,
    CommandAccepted,
    CommandBus,
    DeployReleaseCommand,
    GetCommandQuery,
    GetDeploymentQuery,
    GetReleaseDiagnosticsQuery,
    GetReleaseQuery,
    ListCommandsQuery,
    ListReleasesQuery,
    NotFound,
    Principal,
    PromoteReleaseCommand,
    QueryBus,
    RedeployReleaseCommand,
    ResumeWebsiteCommand,
    RollbackReleaseCommand,
    SuspendWebsiteCommand,
    Unauthenticated,
    in_flight_command_id,
)
from provisioning.core.domain import (
    MAX_IDEMPOTENCY_KEY_LEN,
    CommandStatus,
    InvalidId,
    InvalidIdempotencyKey,
    error_chain,
    parse_id,
    validate_idempotency_key,
)
from provisioning.presentation.operations.errors import (
    INTERNAL,
    Code,
    FieldCode,
    FieldError,
    MalformedRequest,
    classify,
    field_errors,
    message,
    status_and_code,
)
from provisioning.presentation.operations.operations import (
    INPUT_COMMAND_ID,
    INPUT_CURSOR,
    INPUT_IDEMPOTENCY_KEY,
    INPUT_LIMIT,
    INPUT_PROJECT_ID,
    INPUT_RELEASE_ID,
    Operation,
    field_name,
    lookup,
)
from provisioning.presentation.operations.paging import (
    parse_command_cursor,
    parse_limit,
    parse_release_cursor,
)
from provisioning.presentation.operations.problem import (
    problem_body,
    problem_instance,
    problem_media_type,
)
from provisioning.presentation.operations.request_id import current_request_id
from provisioning.presentation.operations.wire import (
    Body,
    Page,
    accepted_body,
    admin_command_list_body,
    command_body,
    command_list_body,
    deployment_body,
    diagnostics_body,
    envelope,
    list_result,
    release_body,
    release_list_body,
)


class CallLogger(Protocol):
    """What the operation layer logs through: one record per call (telemetry's logger)."""

    def info(self, event: str, /, **fields: object) -> object: ...

    def error(self, event: str, /, **fields: object) -> object: ...


type CorrelationBinder = Callable[[str], AbstractContextManager[object]]
"""Makes a correlation ID the current one for the logs of a block (telemetry's
``correlation_scope``)."""

type TraceIdSource = Callable[[], str | None]
"""The trace ID of the current span when the request is sampled, else None (telemetry's
``sampled_trace_id``); the host passes it so that presentation imports no infrastructure."""

type Data = Body | list[Body]
"""A success's ``data``: an object, or a list's items."""


def utc_now() -> datetime:
    """The current time in UTC: the default clock of ``meta.timestamp``."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Outcome:
    """The result of one call in transport-neutral form: the REST status, the error code (empty
    on success) and the MCP body: for a success the REST ``data`` object, or for a list
    ``{"items", "page"}`` (decision 0023 G7); for an error the Problem without ``instance``
    (decision 0018, 0023 G5). The members a body is built from are kept, so that REST can wrap a
    success in the envelope with its path (G2). MCP sends ``body`` as the tool result's
    structured content, marked as an error when ``code`` is set; REST sends ``rest_body`` with
    ``media_type(accept)``."""

    status: int
    code: str
    body: bytes
    request_id: str = ""
    trace_id: str | None = None
    errors: tuple[FieldError, ...] = ()
    command_id: UUID | None = None
    title: str | None = None
    data: Data | None = None
    page: Page | None = None
    at: datetime | None = None

    def is_error(self) -> bool:
        return self.code != ""

    def media_type(self, accept: str | None = None) -> str:
        """``application/json`` for a success. For an error, ``problem_media_type(accept)``:
        ``application/problem+json`` unless the request's ``Accept`` header allows only
        ``application/json`` (spec 6.3 of the Project service; the body is the same)."""
        return problem_media_type(accept) if self.is_error() else "application/json"

    def rest_body(self, path: str) -> bytes:
        """The REST body for the request ``path`` (ASGI's ``scope["path"]``, which excludes the
        query string): a success in the envelope with ``meta.path`` (decision 0023 G2); the
        Problem with ``instance``. Both carry the path percent-encoded, never raw caller text."""
        if not self.is_error():
            return _encode(
                envelope(
                    self.data if self.data is not None else {},
                    request_id=self.request_id,
                    trace_id=self.trace_id,
                    path=problem_instance(path),
                    status=self.status,
                    page=self.page,
                    now=self.at if self.at is not None else utc_now(),
                )
            )
        return problem_body(
            self.code,
            self.status,
            request_id=self.request_id,
            trace_id=self.trace_id,
            errors=self.errors,
            command_id=self.command_id,
            instance=path,
            title=self.title,
        )


class _ResultMismatch(Exception):
    """A result naming another project, release or command than the request. It has no row, so it
    is internal: the response would otherwise show data the caller did not ask for."""


class _UnknownOperation(Exception):
    """A call for an action that is not an operation."""


class _NoCorrelationId(Exception):
    """The correlation ID function returned a value outside the allowed form; the call cannot be
    identified in logs and work rows, so it is not run."""


_CORRELATION_FORM: Final = re.compile(rf"[A-Za-z0-9._:-]{{1,{MAX_CORRELATION_ID_LEN}}}", re.ASCII)
_TRACE_FORM: Final = re.compile(r"[0-9a-f]{32}", re.ASCII)
_CODES: Final = frozenset(Code)


def random_correlation_id() -> str:
    """The current request ID (the middleware accepted or made it, spec E8), else a fresh UUIDv7.
    The command's correlation ID is the request ID, so one value follows the request into the
    work row, the logs and the gRPC calls."""
    return current_request_id() or str(uuid.uuid7())


# The groups of ``Invoker._dispatch``: the seven writes go to the command bus, the three listings
# and the other reads to the query bus (decision 0025 CQ11).
_WRITES: Final = frozenset(
    {
        Action.DEPLOY_RELEASE,
        Action.PROMOTE_RELEASE,
        Action.ROLLBACK_RELEASE,
        Action.SUSPEND_WEBSITE,
        Action.RESUME_WEBSITE,
        Action.REDEPLOY_RELEASE,
        Action.BUILD_RELEASE,
    }
)
_LISTINGS: Final = frozenset(
    {Action.ADMIN_LIST_COMMANDS, Action.LIST_RELEASES, Action.LIST_COMMANDS}
)


@dataclass(frozen=True, slots=True)
class _Args:
    project_id: UUID | None = None
    release_id: UUID | None = None
    command_id: UUID | None = None
    key: str = ""
    limit: int = 0
    cursor: str | None = None


class Invoker:
    """Runs the fourteen operations for both transports: ``commands`` takes the seven writes and
    ``queries`` the seven reads (decision 0025 CQ11). Every argument is required;
    ``request_timeout`` (HTTP_REQUEST_TIMEOUT) bounds each application call and must be
    positive; ``trace_id`` gives the ``traceId`` of a Problem and of a success's ``meta``
    (decision 0023 G2, G5). ``clock`` gives ``meta.timestamp``; the current UTC time unless a
    test fixes it."""

    __slots__ = (
        "_bind",
        "_clock",
        "_commands",
        "_log",
        "_new_correlation_id",
        "_queries",
        "_timeout",
        "_trace_id",
    )

    def __init__(
        self,
        *,
        commands: CommandBus,
        queries: QueryBus,
        request_timeout: timedelta,
        new_correlation_id: Callable[[], str],
        logger: CallLogger,
        bind_correlation: CorrelationBinder,
        trace_id: TraceIdSource,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if request_timeout <= timedelta(0):
            raise ValueError("ops: request_timeout must be positive")
        self._commands = commands
        self._queries = queries
        self._timeout = request_timeout.total_seconds()
        self._new_correlation_id = new_correlation_id
        self._log = logger
        self._bind = bind_correlation
        self._trace_id = trace_id
        self._clock = clock

    async def invoke(
        self,
        principal: Principal | None,
        action: Action,
        inputs: Mapping[str, str],
        *,
        actor_kind: AuditActorKind = AuditActorKind.USER,
        field_of: Callable[[str], str] = field_name,
    ) -> Outcome:
        """Run one operation for the principal the transport's authentication verified (None when
        there is none: 401). ``actor_kind`` names the transport's caller in a mutation's audit
        event: a person on REST, an agent on MCP. ``field_of`` names an input in a field error as
        the transport's client sent it: REST's path parameter or header by default, the tool
        argument on MCP. Never raises but ``CancelledError``."""
        start = time.monotonic()
        corr = self._correlation_id()
        key_field = field_of(INPUT_IDEMPOTENCY_KEY)
        op = lookup(action)
        if op is None:
            return self._finish(
                action, None, corr, start, key_field, error=_UnknownOperation(str(action))
            )
        if not corr:
            return self._finish(action, op, corr, start, key_field, error=_NoCorrelationId())
        if principal is None:
            return self._finish(
                action, op, corr, start, key_field, error=Unauthenticated("no principal")
            )
        try:
            args = _parse(op, inputs, field_of)
            with self._bind(corr):
                async with asyncio.timeout(self._timeout):
                    status, data, page, replayed = await self._dispatch(
                        op.action, principal, args, corr, actor_kind
                    )
        except Exception as exc:
            return self._finish(action, op, corr, start, key_field, error=exc)
        return self._finish(
            action,
            op,
            corr,
            start,
            key_field,
            status=status,
            data=data,
            page=page,
            replayed=replayed,
        )

    def bind_correlation(self, value: str) -> AbstractContextManager[object]:
        """Make ``value`` the current correlation ID of the logs and the outgoing calls."""
        return self._bind(value)

    def reject(
        self,
        action: Action | None,
        error: BaseException,
        *,
        field_of: Callable[[str], str] = field_name,
    ) -> Outcome:
        """The error outcome of a failure a transport found before ``invoke`` could run: an input
        it could not decode, a body over its limit, a failed authentication, the in-flight cap.
        ``action`` is None when no operation is known yet (the MCP endpoint, an unknown route).
        ``field_of`` names the key as in ``invoke``; such failures carry their own field errors,
        and only an error of the application could name the key."""
        op = None if action is None else lookup(action)
        return self._finish(
            action,
            op,
            self._correlation_id(),
            time.monotonic(),
            field_of(INPUT_IDEMPOTENCY_KEY),
            error=error,
        )

    async def _dispatch(
        self,
        action: Action,
        principal: Principal,
        args: _Args,
        corr: str,
        actor_kind: AuditActorKind,
    ) -> tuple[int, Data, Page | None, bool]:
        """Send ``action`` to its group: the writes to the command bus, the single reads and the
        listings to the query bus. The project ID is checked first for every operation."""
        project_id = _required(args.project_id)
        if action in _WRITES:
            return await self._write(action, principal, project_id, args, corr, actor_kind)
        if action in _LISTINGS:
            return await self._list(action, principal, project_id, args)
        return await self._read(action, principal, project_id, args)

    async def _write(
        self,
        action: Action,
        principal: Principal,
        project_id: UUID,
        args: _Args,
        corr: str,
        actor_kind: AuditActorKind,
    ) -> tuple[int, Data, Page | None, bool]:
        commands = self._commands
        match action:
            case Action.DEPLOY_RELEASE | Action.PROMOTE_RELEASE | Action.ROLLBACK_RELEASE:
                release_id = _required(args.release_id)
                release_command = {
                    Action.DEPLOY_RELEASE: DeployReleaseCommand,
                    Action.PROMOTE_RELEASE: PromoteReleaseCommand,
                    Action.ROLLBACK_RELEASE: RollbackReleaseCommand,
                }[action]
                accepted = await commands.send(
                    release_command(
                        principal=principal,
                        project_id=project_id,
                        release_id=release_id,
                        idempotency_key=args.key,
                        correlation_id=corr,
                        actor_kind=actor_kind,
                    )
                )
                return _accepted(project_id, accepted)
            case Action.SUSPEND_WEBSITE | Action.RESUME_WEBSITE | Action.REDEPLOY_RELEASE:
                admin_command = {
                    Action.SUSPEND_WEBSITE: SuspendWebsiteCommand,
                    Action.RESUME_WEBSITE: ResumeWebsiteCommand,
                    Action.REDEPLOY_RELEASE: RedeployReleaseCommand,
                }[action]
                accepted = await commands.send(
                    admin_command(
                        principal=principal,
                        project_id=project_id,
                        idempotency_key=args.key,
                        correlation_id=corr,
                        actor_kind=actor_kind,
                    )
                )
                return _accepted(project_id, accepted)
            case Action.BUILD_RELEASE:
                release_id = _required(args.release_id)
                # The 202 body is the release after the start (the contract's Release schema),
                # which the handler reads under the build_release permission itself.
                release = await commands.send(
                    BuildReleaseCommand(
                        principal=principal,
                        project_id=project_id,
                        release_id=release_id,
                        correlation_id=corr,
                        actor_kind=actor_kind,
                    )
                )
                if release.project_id != project_id or release.release_id != release_id:
                    raise _ResultMismatch("build")
                return 202, release_body(release), None, False
        raise _UnknownOperation(str(action))

    async def _read(
        self, action: Action, principal: Principal, project_id: UUID, args: _Args
    ) -> tuple[int, Data, Page | None, bool]:
        queries = self._queries
        match action:
            case Action.GET_RELEASE:
                release_id = _required(args.release_id)
                release = await queries.send(
                    GetReleaseQuery(
                        principal=principal, project_id=project_id, release_id=release_id
                    )
                )
                if release.project_id != project_id or release.release_id != release_id:
                    raise _ResultMismatch("release")
                return 200, release_body(release), None, False
            case Action.GET_COMMAND:
                command_id = _required(args.command_id)
                command = await queries.send(
                    GetCommandQuery(
                        principal=principal, project_id=project_id, command_id=command_id
                    )
                )
                if command.project_id != project_id or command.command_id != command_id:
                    raise _ResultMismatch("command")
                return 200, command_body(command), None, False
            case Action.GET_DEPLOYMENT:
                deployment = await queries.send(
                    GetDeploymentQuery(principal=principal, project_id=project_id)
                )
                if deployment.project_id != project_id:
                    raise _ResultMismatch("deployment")
                return 200, deployment_body(deployment), None, False
            case Action.GET_RELEASE_DIAGNOSTICS:
                release_id = _required(args.release_id)
                diagnostics = await queries.send(
                    GetReleaseDiagnosticsQuery(
                        principal=principal, project_id=project_id, release_id=release_id
                    )
                )
                if diagnostics.release_id != release_id:
                    raise _ResultMismatch("diagnostics")
                return 200, diagnostics_body(diagnostics), None, False
        raise _UnknownOperation(str(action))

    async def _list(
        self, action: Action, principal: Principal, project_id: UUID, args: _Args
    ) -> tuple[int, Data, Page | None, bool]:
        queries = self._queries
        match action:
            case Action.ADMIN_LIST_COMMANDS:
                admin_page = await queries.send(
                    AdminListCommandsQuery(
                        principal=principal,
                        project_id=project_id,
                        limit=args.limit,
                        cursor=parse_command_cursor(args.cursor),
                    )
                )
                if any(item.project_id != project_id for item in admin_page.items):
                    raise _ResultMismatch("admin commands")
                items, page = admin_command_list_body(admin_page, limit=args.limit)
                return 200, items, page, False
            case Action.LIST_RELEASES:
                releases = await queries.send(
                    ListReleasesQuery(
                        principal=principal,
                        project_id=project_id,
                        page_size=args.limit,
                        after=parse_release_cursor(args.cursor),
                    )
                )
                if any(item.project_id != project_id for item in releases.items):
                    raise _ResultMismatch("releases")
                items, page = release_list_body(releases, limit=args.limit)
                return 200, items, page, False
            case Action.LIST_COMMANDS:
                commands_page = await queries.send(
                    ListCommandsQuery(
                        principal=principal,
                        project_id=project_id,
                        page_size=args.limit,
                        after=parse_command_cursor(args.cursor),
                    )
                )
                if any(item.project_id != project_id for item in commands_page.items):
                    raise _ResultMismatch("commands")
                items, page = command_list_body(commands_page, limit=args.limit)
                return 200, items, page, False
        raise _UnknownOperation(str(action))

    def _finish(
        self,
        action: Action | None,
        op: Operation | None,
        corr: str,
        start: float,
        key_field: str,
        *,
        status: int = 0,
        data: Data | None = None,
        page: Page | None = None,
        replayed: bool = False,
        error: BaseException | None = None,
    ) -> Outcome:
        if error is None:
            if data is None:
                data = {}
            try:
                outcome = Outcome(
                    status=status,
                    code="",
                    body=_encode(_mcp_result(data, page)),
                    request_id=corr,
                    trace_id=self._sampled_trace_id(),
                    data=data,
                    page=page,
                    at=self._clock(),
                )
            except (TypeError, ValueError) as exc:
                error = exc
            else:
                self._record(action, corr, start, outcome, replayed, None)
                return outcome
        problem = self._problem(op, corr, error, key_field)
        self._record(action, corr, start, problem, False, error)
        return problem

    def _problem(
        self, op: Operation | None, corr: str, error: BaseException, key_field: str
    ) -> Outcome:
        """The Problem of an error (decision 0023 G5, G6, G14): its class's status and code, the
        operation's own not-found code once access passed, internal for a status the operation
        does not declare (a 409 on a read), the field errors of a 400 (the key named
        ``key_field``, as the transport's client sent it) and the command in flight of a
        COMMAND_IN_PROGRESS."""
        row, _ = classify(error)
        status, code = status_and_code(
            row, error, op.answers if op is not None else lambda _status, _code: True
        )
        if op is not None and not op.answers(status, code):
            row = INTERNAL
            status, code = row.status, row.code
        if op is not None and row.error is NotFound:
            code = op.not_found
        title = None if code in _CODES else message(row.code)
        errors = field_errors(error, key_field=key_field) if code == Code.VALIDATION_FAILED else ()
        command_id = in_flight_command_id(error) if code == Code.COMMAND_IN_PROGRESS else None
        request_id = corr or str(uuid.uuid7())
        trace_id = self._sampled_trace_id()
        return Outcome(
            status=status,
            code=code,
            body=problem_body(
                code,
                status,
                request_id=request_id,
                trace_id=trace_id,
                errors=errors,
                command_id=command_id,
                title=title,
            ),
            request_id=request_id,
            trace_id=trace_id,
            errors=errors,
            command_id=command_id,
            title=title,
        )

    def _sampled_trace_id(self) -> str | None:
        """The trace ID when the request is sampled and the ID has the W3C form, else None."""
        trace_id = self._trace_id()
        return trace_id if trace_id is not None and _TRACE_FORM.fullmatch(trace_id) else None

    def _record(
        self,
        action: Action | None,
        corr: str,
        start: float,
        outcome: Outcome,
        replayed: bool,
        error: BaseException | None,
    ) -> None:
        """One record with an allow-list of fields. The error text is logged for server errors
        only; it never contains the token, the idempotency key or a body (core errors carry
        identifiers and statuses only, and the logger scrubs credentials)."""
        operation = "" if action is None else action.value
        duration_ms = int((time.monotonic() - start) * 1000)
        if outcome.status >= 500:
            self._log.error(
                "request finished",
                operation=operation,
                status=outcome.status,
                duration_ms=duration_ms,
                correlation_id=corr or None,
                error_code=outcome.code or None,
                error=None if error is None else _describe(error),
            )
            return
        self._log.info(
            "request finished",
            operation=operation,
            status=outcome.status,
            duration_ms=duration_ms,
            correlation_id=corr or None,
            error_code=outcome.code or None,
            replayed=True if replayed else None,
        )

    def _correlation_id(self) -> str:
        value = self._new_correlation_id()
        return value if _CORRELATION_FORM.fullmatch(value) else ""


def _parse(op: Operation, inputs: Mapping[str, str], field_of: Callable[[str], str]) -> _Args:
    """Parse in the operation's order, so the first invalid input is the one the Problem names
    (decision 0023 G5). A name the operation does not take, a missing or invalid identifier and a
    missing or invalid key are ``MalformedRequest``, the last three with their field; an invalid
    limit too (the cursor is parsed with the listing it belongs to)."""
    for name in inputs:
        if name not in op.inputs:
            raise MalformedRequest("unknown input")
    values: dict[str, Any] = {}
    for name in op.inputs:
        value = inputs.get(name)
        if name == INPUT_LIMIT:
            values["limit"] = parse_limit(value)
            continue
        if name == INPUT_CURSOR:
            values["cursor"] = value
            continue
        if name == INPUT_IDEMPOTENCY_KEY:
            values["key"] = _parse_key(value, field_of(name))
            continue
        if value is None:
            raise _invalid(field_of(name), FieldCode.REQUIRED)
        try:
            values[_FIELD_OF_INPUT[name]] = parse_id(value)
        except InvalidId:
            raise _invalid(field_of(name), FieldCode.INVALID_FORMAT) from None
    return _Args(**values)


def _parse_key(value: str | None, field: str) -> str:
    if not value:
        raise _invalid(field, FieldCode.REQUIRED)
    if len(value.encode("utf-8", "surrogatepass")) > MAX_IDEMPOTENCY_KEY_LEN:
        raise _invalid(field, FieldCode.TOO_LONG)
    try:
        validate_idempotency_key(value)
    except InvalidIdempotencyKey:
        raise _invalid(field, FieldCode.INVALID_FORMAT) from None
    return value


def _invalid(field: str, code: FieldCode) -> MalformedRequest:
    """The error of one bad input; it names the input, never the value."""
    return MalformedRequest("invalid input", errors=(FieldError(field, code),))


_FIELD_OF_INPUT: Final = {
    INPUT_PROJECT_ID: "project_id",
    INPUT_RELEASE_ID: "release_id",
    INPUT_COMMAND_ID: "command_id",
}


def _accepted(project_id: UUID, accepted: CommandAccepted) -> tuple[int, Data, None, bool]:
    """The 202 of an accepted command, after checking that it is of the request's project."""
    if accepted.project_id != project_id or accepted.command_id.int == 0:
        raise _ResultMismatch("accepted command")
    CommandStatus(accepted.status)
    return 202, accepted_body(accepted), None, accepted.replayed


def _required(value: UUID | None) -> UUID:
    if value is None:
        raise InvalidId("missing")
    return value


def _mcp_result(data: Data, page: Page | None) -> Body:
    """The MCP result of a success: the REST ``data`` object, or for a list (a page is set)
    ``{"items": <data>, "page": <meta.page>}`` (decision 0023 G7)."""
    if page is not None and isinstance(data, list):
        return list_result(data, page)
    if isinstance(data, dict):
        return data
    raise TypeError("ops: a success without a page has object data")


def _encode(body: Body) -> bytes:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _describe(error: BaseException) -> str:
    """The class and message of each error in the cause chain, outermost first."""
    return " <- ".join(f"{type(e).__name__}: {e}" for e in error_chain(error))
