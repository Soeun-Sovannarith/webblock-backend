"""Adapters for the Project service's contracts (decision 0017): the access check over HTTP
(``PROJECT_ACCESS_MODE=project-service``)."""

from provisioning.infrastructure.project.access import (
    ACCESS_CHECK_PATH,
    CALL_TIMEOUT,
    DENIAL_REASONS,
    MAX_RESPONSE_BYTES,
    ProjectServiceAccess,
)

__all__ = [
    "ACCESS_CHECK_PATH",
    "CALL_TIMEOUT",
    "DENIAL_REASONS",
    "MAX_RESPONSE_BYTES",
    "ProjectServiceAccess",
]
