"""``ProjectAccess`` over gRPC ``CheckAccess`` (decision 0022 E7), selected by
``PROJECT_ACCESS_MODE=project-grpc`` in either profile. Same outcomes as ``access.py``:

- ``allowed``: allowed;
- not allowed with ``NOT_FOUND``, ``NOT_MANAGER``, ``PROJECT_DELETED`` or ``PROJECT_SUSPENDED``:
  ``ProjectAccessDenied`` (the Project service decides which actions a deleted or suspended
  project still allows);
- any failed call, an unknown reason, or a denial without a reason: ``ProjectAccessUnavailable``;
  a failed call's ``ErrorInfo.reason`` and a passed deadline ride on it (decision 0023 G14).

A principal without a platform user ID is denied without a call. The call carries the user as
``x-user-id`` beside the body's ``user_id`` (FR-8); no token is sent: the mesh is the gate (decision
0019). Logs carry the project, the action and the reason, never the user ID.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

import grpc
import grpc.aio

from provisioning.core.application import (
    Action,
    Principal,
    ProjectAccessUnavailable,
)
from provisioning.infrastructure.grpc._gen.webblock.project.v1 import access_pb2
from provisioning.infrastructure.grpc.call import CallFailure, unary
from provisioning.infrastructure.project.access import (
    DENIAL_REASONS,
    denied,
    failed_internally,
    user_id_or_deny,
)
from provisioning.infrastructure.telemetry import (
    Logger,
    current_correlation_id,
    new_correlation_id,
)

CHECK_ACCESS_METHOD: Final = "/webblock.project.v1.AccessService/CheckAccess"
_COMPONENT: Final = "project-access"


class GrpcProjectAccess:
    """Implements ``ProjectAccess`` over a gRPC channel the host owns and closes."""

    __slots__ = ("_channel", "_log")

    def __init__(self, channel: grpc.aio.Channel, logger: Logger) -> None:
        self._channel = channel
        self._log = logger

    def __repr__(self) -> str:
        return "GrpcProjectAccess()"

    async def check_access(self, principal: Principal, project_id: UUID, action: Action) -> None:
        user_id = user_id_or_deny(principal, project_id, action, self._log)
        request = access_pb2.CheckAccessRequest(
            user_id=str(user_id), project_id=str(project_id), action=action.value
        )
        try:
            answer = await unary(
                self._channel,
                CHECK_ACCESS_METHOD,
                request,
                access_pb2.CheckAccessResponse,
                request_id=current_correlation_id() or new_correlation_id(),
                user_id=user_id,
            )
        except CallFailure as failure:
            self._unavailable(project_id, action, failure.code.name)
            raise ProjectAccessUnavailable(
                f"project service: {failure.code.name}",
                upstream_reason=failure.reason,
                deadline=failure.code is grpc.StatusCode.DEADLINE_EXCEEDED,
            ) from None
        except Exception as exc:  # anything else fails closed, logged by class only
            raise failed_internally(self._log, project_id, action, exc) from None
        if answer.allowed:
            return
        if answer.reason not in DENIAL_REASONS:
            cause = "unknown_reason" if answer.reason else "no_reason"
            self._unavailable(project_id, action, cause)
            raise ProjectAccessUnavailable(f"project service: {cause}")
        raise denied(self._log, project_id, action, answer.reason)

    def _unavailable(self, project_id: UUID, action: Action, cause: str) -> None:
        self._log.warning(
            "project access check failed",
            component=_COMPONENT,
            project_id=project_id,
            action=action,
            reason=cause,
        )
