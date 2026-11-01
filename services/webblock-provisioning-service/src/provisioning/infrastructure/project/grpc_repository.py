"""The repository registry and locator over Project's gRPC ``GetRepository`` (decision 0022 E7),
selected by ``REPOSITORY_MODE=project-grpc``. The proto is a draft that Project has not accepted
(OD-01), so the mode is prototype only and production refuses it.

- ``registered_repository``: the repository ID and sub-domain of the project, or None when Project
  answers ``NOT_FOUND`` with the reason ``REPOSITORY_NOT_FOUND``. An answer that breaks the
  contract (an ID outside 1 to 2^53-1, a sub-domain that is not a DNS label, an empty clone URL)
  is a ``ValueError``, and any failed call a ``DependencyUnavailable``: both are unknown answers
  that the inbox retries.
- ``repository_url``: asks again and returns the clone URL; every failure, a missing repository
  or a different repository ID is a ``DependencyUnavailable``, the one error the build start
  treats as "try later".

No token is sent: the mesh is the gate (decision 0019). Logs carry the project and the reason,
never the clone URL.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

import grpc
import grpc.aio

from provisioning.core.application import DependencyUnavailable, RegisteredRepository
from provisioning.core.domain import DomainError, parse_repository_id, validate_subdomain
from provisioning.infrastructure.grpc._gen.webblock.project.v1 import repository_pb2
from provisioning.infrastructure.grpc.call import CallFailure, unary
from provisioning.infrastructure.telemetry import (
    Logger,
    current_correlation_id,
    new_correlation_id,
)

GET_REPOSITORY_METHOD: Final = "/webblock.project.v1.RepositoryService/GetRepository"
NOT_FOUND_REASON: Final = "REPOSITORY_NOT_FOUND"
_COMPONENT: Final = "project-repository"


class GrpcRepositories:
    """Implements ``RepositoryRegistry`` and ``RepositoryLocator`` over a gRPC channel the host
    owns and closes."""

    __slots__ = ("_channel", "_log")

    def __init__(self, channel: grpc.aio.Channel, logger: Logger) -> None:
        self._channel = channel
        self._log = logger

    def __repr__(self) -> str:
        return "GrpcRepositories()"

    async def registered_repository(self, project_id: UUID) -> RegisteredRepository | None:
        answer = await self._get(project_id)
        if answer is None:
            return None
        try:
            parse_repository_id(answer.repository_id)
            validate_subdomain(answer.subdomain)
        except DomainError:
            self._bad(project_id, "invalid_answer")
            raise ValueError("project service: invalid repository answer") from None
        if not answer.clone_url:
            self._bad(project_id, "no_clone_url")
            raise ValueError("project service: no clone URL")
        return RegisteredRepository(repository_id=answer.repository_id, subdomain=answer.subdomain)

    async def repository_url(self, project_id: UUID, repository_id: int) -> str:
        answer = await self._get(project_id)
        if answer is None:
            raise DependencyUnavailable("project service: no repository for the project")
        if answer.repository_id != repository_id:
            self._bad(project_id, "repository_changed")
            raise DependencyUnavailable("project service: a different repository is registered")
        if not answer.clone_url:
            self._bad(project_id, "no_clone_url")
            raise DependencyUnavailable("project service: no clone URL")
        return answer.clone_url

    async def _get(self, project_id: UUID) -> repository_pb2.GetRepositoryResponse | None:
        """Project's answer, None when it has no repository for the project. Every failure is an
        ordinary error: a ``DependencyUnavailable``, never one the inbox treats as permanent."""
        try:
            return await unary(
                self._channel,
                GET_REPOSITORY_METHOD,
                repository_pb2.GetRepositoryRequest(project_id=str(project_id)),
                repository_pb2.GetRepositoryResponse,
                request_id=current_correlation_id() or new_correlation_id(),
            )
        except CallFailure as failure:
            if failure.code is grpc.StatusCode.NOT_FOUND and failure.reason == NOT_FOUND_REASON:
                return None
            self._bad(project_id, failure.code.name)
            raise DependencyUnavailable(
                f"project service: {failure.code.name}",
                upstream_reason=failure.reason,
                deadline=failure.code is grpc.StatusCode.DEADLINE_EXCEEDED,
            ) from None
        except Exception as exc:  # a closed channel or a serialization error: logged by class only
            self._log.warning(
                "repository lookup failed",
                component=_COMPONENT,
                project_id=project_id,
                reason="internal",
                error=type(exc).__name__,
            )
            raise DependencyUnavailable("project service: internal") from None

    def _bad(self, project_id: UUID, cause: str) -> None:
        self._log.warning(
            "repository lookup failed", component=_COMPONENT, project_id=project_id, reason=cause
        )
