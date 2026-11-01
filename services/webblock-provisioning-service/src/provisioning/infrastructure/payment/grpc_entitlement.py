"""``Entitlement`` and ``HostingEntitlement`` over gRPC ``GetEntitlement`` (decision 0022 E7).
Prototype only: the proto is a draft and Payment's contract is open (OD-04), so both modes that
select this adapter (ENTITLEMENT_MODE and HOSTING_ENTITLEMENT_MODE ``payment-grpc``) are refused in
the production profile.

Payment's answer for a project holds ``hosting`` and ``publish``:

- ``check(DEPLOY)`` needs ``HOSTING_ENTITLED`` and ``PUBLISH_ALLOWED``; ``check(PROMOTE)`` needs
  ``HOSTING_ENTITLED`` alone. ``HOSTING_ENDED`` and, for a deploy, ``PUBLISH_LIMIT_REACHED`` are
  ``LimitReached``.
- ``entitlement_after`` is True for ``HOSTING_ENTITLED``, False for ``HOSTING_ENDED``. The current
  answer wins over the event (Payment contract 2.4: a stale event never wins), so the event key is
  not sent.
- A ``*_UNSPECIFIED`` value, an enum number this build does not know (a newer Payment), an
  answer whose ``project_id`` is set and names another project, a failed call or any other error
  is "could not be determined": ``DependencyUnavailable`` from ``check``,
  None from ``entitlement_after``. It is never entitled.

No token is sent (the mesh is the gate, decision 0019). Logs carry the project and the reason, and
the class name of an unexpected error, never its text.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

import grpc
import grpc.aio

from provisioning.core.application import DependencyUnavailable, LimitReached
from provisioning.core.domain import CommandKind
from provisioning.infrastructure.grpc._gen.webblock.payment.v1 import entitlement_pb2
from provisioning.infrastructure.grpc.call import CallFailure, unary
from provisioning.infrastructure.telemetry import (
    Logger,
    current_correlation_id,
    new_correlation_id,
)

GET_ENTITLEMENT_METHOD: Final = "/webblock.payment.v1.EntitlementService/GetEntitlement"
_COMPONENT: Final = "payment-entitlement"


class _Undetermined(Exception):
    """The answer cannot be used; ``reason`` is logged and never reaches a caller. A failed call
    keeps the ``ErrorInfo.reason`` Payment sent and whether its deadline passed (decision 0023
    G14), for the ``DependencyUnavailable`` it becomes."""

    def __init__(
        self,
        reason: str,
        *,
        logged: bool = False,
        upstream_reason: str | None = None,
        deadline: bool = False,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.logged = logged
        self.upstream_reason = upstream_reason
        self.deadline = deadline


class GrpcEntitlement:
    """Implements ``Entitlement`` and ``HostingEntitlement`` over a channel the host owns."""

    __slots__ = ("_channel", "_log")

    def __init__(self, channel: grpc.aio.Channel, logger: Logger) -> None:
        self._channel = channel
        self._log = logger

    def __repr__(self) -> str:
        return "GrpcEntitlement()"

    async def check(self, project_id: UUID, kind: CommandKind) -> None:
        if kind not in (CommandKind.DEPLOY, CommandKind.PROMOTE):
            self._warn(project_id, "unsupported_kind")
            raise DependencyUnavailable(f"payment entitlement: no check for {kind}")
        try:
            answer = await self._get(project_id)
            hosting = _hosting(answer)
            if hosting == entitlement_pb2.HOSTING_ENDED:
                self._info(project_id, "hosting_ended")
                raise LimitReached(f"{kind} of project {project_id}: hosting ended")
            if kind is CommandKind.PROMOTE:
                return
            publish = _publish(answer)
        except _Undetermined as undetermined:
            self._log_undetermined(project_id, undetermined)
            raise DependencyUnavailable(
                f"payment entitlement: {undetermined.reason}",
                upstream_reason=undetermined.upstream_reason,
                deadline=undetermined.deadline,
            ) from None
        if publish == entitlement_pb2.PUBLISH_LIMIT_REACHED:
            self._info(project_id, "publish_limit_reached")
            raise LimitReached(f"{kind} of project {project_id}: publish limit reached")

    async def entitlement_after(self, project_id: UUID, event_key: str) -> bool | None:
        del event_key  # the port's signature; Payment's answer is the current entitlement
        try:
            hosting = _hosting(await self._get(project_id))
        except _Undetermined as undetermined:
            self._log_undetermined(project_id, undetermined)
            return None
        return hosting == entitlement_pb2.HOSTING_ENTITLED

    async def _get(self, project_id: UUID) -> entitlement_pb2.GetEntitlementResponse:
        try:
            answer = await unary(
                self._channel,
                GET_ENTITLEMENT_METHOD,
                entitlement_pb2.GetEntitlementRequest(project_id=str(project_id)),
                entitlement_pb2.GetEntitlementResponse,
                request_id=current_correlation_id() or new_correlation_id(),
            )
        except CallFailure as failure:
            self._warn(project_id, failure.code.name)
            raise _Undetermined(
                failure.code.name,
                logged=True,
                upstream_reason=failure.reason,
                deadline=failure.code is grpc.StatusCode.DEADLINE_EXCEEDED,
            ) from None
        except Exception as exc:  # anything else is undetermined, logged by class only
            self._log.warning(
                "payment entitlement check failed",
                component=_COMPONENT,
                project_id=project_id,
                reason="internal",
                error=type(exc).__name__,
            )
            raise _Undetermined("internal", logged=True) from None
        if answer.project_id and not _same_project(answer.project_id, project_id):
            raise _Undetermined("project_mismatch")
        return answer

    def _log_undetermined(self, project_id: UUID, undetermined: _Undetermined) -> None:
        if not undetermined.logged:
            self._warn(project_id, undetermined.reason)

    def _warn(self, project_id: UUID, reason: str) -> None:
        self._log.warning(
            "payment entitlement could not be determined",
            component=_COMPONENT,
            project_id=project_id,
            reason=reason,
        )

    def _info(self, project_id: UUID, reason: str) -> None:
        self._log.info(
            "payment entitlement refused",
            component=_COMPONENT,
            project_id=project_id,
            reason=reason,
        )


def _same_project(answered: str, project_id: UUID) -> bool:
    """Whether the answer's project ID names ``project_id``; a value that is not a UUID does
    not."""
    try:
        return UUID(answered) == project_id
    except ValueError:
        return False


def _hosting(answer: entitlement_pb2.GetEntitlementResponse) -> int:
    """HOSTING_ENTITLED or HOSTING_ENDED; anything else (unspecified or unknown) is undetermined."""
    if answer.hosting in (entitlement_pb2.HOSTING_ENTITLED, entitlement_pb2.HOSTING_ENDED):
        return int(answer.hosting)
    raise _Undetermined("unspecified_hosting" if answer.hosting == 0 else "unknown_hosting")


def _publish(answer: entitlement_pb2.GetEntitlementResponse) -> int:
    if answer.publish in (entitlement_pb2.PUBLISH_ALLOWED, entitlement_pb2.PUBLISH_LIMIT_REACHED):
        return int(answer.publish)
    raise _Undetermined("unspecified_publish" if answer.publish == 0 else "unknown_publish")
