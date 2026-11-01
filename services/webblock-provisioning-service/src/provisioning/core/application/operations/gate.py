"""The gate every operation passes first (decision 0004 section 1, decision 0006 section 6; cut
from the former Service class, decision 0025 CQ8).

1. action permission: the empty principal is refused without asking the policy; any refusal or
   policy fault is ``ActionDenied``; an admin-only action (``Action.is_admin_only``: rollback,
   FR-50, and the four of 0023 G10) is also ``ActionDenied`` unless the principal holds
   ``ADMIN_ROLE``;
2. operation gate: a switched-off operation is ``OperationDisabled`` (``OpenContract`` when a
   contract it needs is open, a production build; task H2) and asks no other service;
3. project access: a nil project ID is ``InvalidId``; an admin-only action, which only an
   ``ADMIN`` reaches, asks nothing more (decision 0019 section 7, OD-45); otherwise no access is
   ``ProjectAccessDenied`` and any other failure is ``ProjectAccessUnavailable`` raised from it;

then each handler validates and runs its use case.
"""

from __future__ import annotations

from uuid import UUID

from provisioning.core.application.ports.dependencies import ActionPolicy, ProjectAccess
from provisioning.core.application.shared.action import ADMIN_ROLE, Action, EnabledOperations
from provisioning.core.application.shared.errors import (
    ActionDenied,
    ProjectAccessDenied,
    ProjectAccessUnavailable,
)
from provisioning.core.application.shared.principal import Principal
from provisioning.core.domain import InvalidId, is_error


class OperationGate:
    """The fixed check order every operation passes before its use case."""

    __slots__ = ("_access", "_enabled", "_policy")

    def __init__(
        self, *, policy: ActionPolicy, access: ProjectAccess, enabled: EnabledOperations
    ) -> None:
        self._policy = policy
        self._access = access
        self._enabled = enabled

    async def check(self, principal: Principal, action: Action, project_id: UUID) -> bool:
        """Return whether project access was skipped because an ``ADMIN`` acts on any project
        (decision 0019 section 7): the mutation's audit event then names an operator (security
        review finding M-2). Raise the first refusal otherwise."""
        # 1. The action permission: the empty principal is permitted nothing whatever the policy
        # says, and any refusal or fault of the policy fails closed.
        if principal.is_empty():
            raise ActionDenied(str(action))
        try:
            self._policy.permit(principal, action)
        except Exception:
            raise ActionDenied(str(action)) from None
        # An admin-only action (rollback, FR-50, and 0023 G10) needs the ADMIN role itself,
        # whatever scope the policy maps the action to: a mapping to a lesser role never opens it.
        if action.is_admin_only() and not principal.has_scope(ADMIN_ROLE):
            raise ActionDenied(str(action))
        # 2. The operation gate: a switched-off operation asks no other service.
        if not self._enabled.enabled(action):
            raise self._enabled.refusal(action)
        # 3. Project access, for a valid project only. An ADMIN takes an admin-only action on any
        # project (decision 0019 section 7, OD-45): no owner check is asked, and the command
        # records the admin's subject. Every other action is asked, an ADMIN's included.
        if project_id.int == 0:
            raise InvalidId("project ID is required")
        if action.is_admin_only():
            return True
        try:
            await self._access.check_access(principal, project_id, action)
        except Exception as exc:
            if is_error(exc, ProjectAccessDenied):
                raise ProjectAccessDenied("no access to the project") from None
            raise ProjectAccessUnavailable("project access unavailable") from exc
        return False
