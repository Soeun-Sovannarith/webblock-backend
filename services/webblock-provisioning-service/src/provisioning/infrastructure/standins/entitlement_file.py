"""The prototype's file-based stand-in for Payment's entitlement (OD-04; decision 0014 section 21
ruling 10), selected by ENTITLEMENT_MODE=prototype-file with ENTITLEMENT_FILE, in the API (at
acceptance) and the worker (when a promote runs). The file is read again whenever it changes, so
withdrawing entitlement between acceptance and run is shown end to end:

    {"default_entitled": true, "projects": {"<project uuid>": false}}

A project the file names takes its value; any other project takes ``default_entitled``. A project
that is not entitled is ``LimitReached`` (limit_reached, 429, at acceptance; entitlement_lost at
run). A file that cannot be read or is invalid is ``EntitlementFileError``: the answer is unknown
(dependency_unavailable), never "entitled" and never a limit, until the file is repaired. No
message names a value from the file or its path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final
from uuid import UUID

from provisioning.core.application import LimitReached
from provisioning.core.domain import CommandKind, DomainError, parse_id
from provisioning.infrastructure.config.files import ConfigFileError, json_object
from provisioning.infrastructure.standins.access import ReloadingFile
from provisioning.infrastructure.telemetry import Logger

MAX_PROJECTS: Final = 10_000
_WHAT: Final = "entitlement file"


class EntitlementFileError(ConfigFileError):
    """The entitlement file cannot be read or is invalid; the message holds no value from it."""


@dataclass(frozen=True, slots=True)
class _Entitlements:
    default: bool
    projects: dict[UUID, bool]

    def entitled(self, project_id: UUID) -> bool:
        return self.projects.get(project_id, self.default)


class PrototypeFileEntitlement:
    """Implements ``Entitlement`` from the entitlement file. A refusal is logged with the project
    and the command kind."""

    __slots__ = ("_file", "_log")

    def __init__(self, file: ReloadingFile[_Entitlements], logger: Logger) -> None:
        self._file = file
        self._log = logger

    @classmethod
    def from_file(cls, path: Path, logger: Logger) -> PrototypeFileEntitlement:
        """Read the file once at start-up; ``EntitlementFileError`` when it cannot be read or is
        invalid."""
        file = ReloadingFile(path, parse_entitlement_file, EntitlementFileError, _WHAT)
        file.load()
        return cls(file, logger)

    async def check(self, project_id: UUID, kind: CommandKind) -> None:
        entitlements = await self._file.current()
        if entitlements.entitled(project_id):
            return
        self._log.info(
            "prototype entitlement file refused the command",
            component="prototype-entitlement",
            project_id=project_id,
            kind=kind,
        )
        raise LimitReached(f"{kind} of project {project_id}: not entitled in the {_WHAT}")


def parse_entitlement_file(raw: bytes) -> _Entitlements:
    """The entitlements of the file (``EntitlementFileError`` naming the problem, never a
    value)."""
    doc = json_object(raw, _WHAT, frozenset({"default_entitled", "projects"}), EntitlementFileError)
    default, items = doc["default_entitled"], doc["projects"]
    if not isinstance(default, bool):
        raise EntitlementFileError(f"{_WHAT}: default_entitled must be true or false")
    if not isinstance(items, dict) or len(items) > MAX_PROJECTS:
        raise EntitlementFileError(
            f"{_WHAT}: projects must be an object of at most {MAX_PROJECTS} project IDs"
        )
    projects: dict[UUID, bool] = {}
    for key, value in items.items():
        try:
            project = parse_id(key)
        except DomainError:
            raise EntitlementFileError(
                f"{_WHAT}: projects must be keyed by canonical UUIDs"
            ) from None
        if not isinstance(value, bool):
            raise EntitlementFileError(f"{_WHAT}: each project must be true or false")
        projects[project] = value
    return _Entitlements(default=default, projects=projects)
