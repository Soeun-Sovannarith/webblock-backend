"""Prototype stand-ins for Project's access check (OD-01) and Payment's entitlement (OD-04),
decision 0014 sections 3 and 21 ruling 10.

- ``prototype-allow`` (PROJECT_ACCESS_MODE, ENTITLEMENT_MODE): every principal may act on every
  project and every project is entitled; each decision is logged.
- ``prototype-file`` (PROJECT_ACCESS_MODE with PROJECT_ACCESS_FILE): a JSON file names the projects
  each token subject may act on, read again whenever it changes, so a refusal can be shown end to
  end while the API runs. The entitlement file is ``entitlement_file.py``.

The access file:

    {"subjects": [{"subject": "prototype-operator", "projects": "*"},
                  {"subject": "tenant-a", "projects": ["<project uuid>", ...]}]}

``"*"`` means every project. A subject the file does not name has no project. The token's scopes
(the action permission, 403) stay in the development token file; this file decides project access
alone (404, decision 0006 section 3).

A file that cannot be read or is invalid fails closed: the answer is unknown
(``AccessFileError``, reported as project_access_unavailable), never "allowed", until the file is
repaired. Writers replace the file atomically (write, then rename). No message names a value from
the file or its path.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from uuid import UUID

from provisioning.core.application import Action, Principal, ProjectAccessDenied
from provisioning.core.domain import CommandKind, DomainError, parse_id
from provisioning.infrastructure.config.files import (
    ConfigFileError,
    check_keys,
    json_object,
    read_file,
)
from provisioning.infrastructure.telemetry import Logger

ALL_PROJECTS: Final = "*"
MAX_SUBJECTS: Final = 1000
MAX_PROJECTS_PER_SUBJECT: Final = 10_000
MAX_SUBJECT_LEN: Final = 255
_WHAT: Final = "project access file"
_GRANT_KEYS: Final = frozenset({"subject", "projects"})

type _Stamp = tuple[int, int, int]


class PrototypeAllowProjectAccess:
    """Implements ProjectAccess for the prototype: every principal may act on every project. The
    decision is logged with the project and the action, never the subject."""

    __slots__ = ("_log",)

    def __init__(self, logger: Logger) -> None:
        self._log = logger

    async def check_access(self, principal: Principal, project_id: UUID, action: Action) -> None:
        del principal  # the stand-in decides without it
        self._log.info(
            "prototype stand-in allowed project access",
            component="prototype-project-access",
            project_id=project_id,
            action=action,
        )


class PrototypeAllowEntitlement:
    """Implements Entitlement for the prototype: every deploy and promote is entitled. The
    decision is logged with the project and the command kind."""

    __slots__ = ("_log",)

    def __init__(self, logger: Logger) -> None:
        self._log = logger

    async def check(self, project_id: UUID, kind: CommandKind) -> None:
        self._log.info(
            "prototype stand-in allowed the entitlement",
            component="prototype-entitlement",
            project_id=project_id,
            kind=kind,
        )


class AccessFileError(ConfigFileError):
    """The access file cannot be read or is invalid; the message holds no value from it."""


class ReloadingFile[T]:
    """A parsed JSON file, parsed again on a worker thread whenever its modification time, size
    or inode changes. ``current`` raises the parse error while the file is unusable (fail
    closed); a repaired file is used again."""

    __slots__ = ("_error", "_parse", "_path", "_stamp", "_value", "_what")

    def __init__(
        self,
        path: Path,
        parse: Callable[[bytes], T],
        error: type[ConfigFileError],
        what: str,
    ) -> None:
        self._path = path
        self._parse = parse
        self._error = error
        self._what = what
        self._stamp: _Stamp | None = None
        self._value: T | None = None

    def load(self) -> T:
        """Read and parse the file now (at start-up); the error class otherwise, with the
        parse's reason."""
        stamp = _stamp_of(self._path)
        if stamp is None:
            raise self._error(f"the {self._what} cannot be read")
        try:
            value = self._parse(read_file(self._path, self._what))
        except ConfigFileError as err:
            raise self._error(str(err)) from None
        self._stamp, self._value = stamp, value
        return value

    async def current(self) -> T:
        await asyncio.to_thread(self._refresh)
        if self._value is None:
            raise self._error(f"the {self._what} cannot be read or is invalid")
        return self._value

    def _refresh(self) -> None:
        stamp = _stamp_of(self._path)
        if stamp is None:
            self._stamp, self._value = None, None
            return
        if stamp == self._stamp:
            return
        try:
            value: T | None = self._parse(read_file(self._path, self._what))
        except ConfigFileError:
            value = None  # fail closed until the file is repaired
        self._stamp, self._value = stamp, value


def _stamp_of(path: Path) -> _Stamp | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size, st.st_ino


@dataclass(frozen=True, slots=True)
class _Grants:
    everything: frozenset[str]
    projects: dict[str, frozenset[UUID]]

    def allows(self, subject: str, project_id: UUID) -> bool:
        return subject in self.everything or project_id in self.projects.get(subject, frozenset())


class PrototypeFileProjectAccess:
    """Implements ProjectAccess from the access file, read again whenever it changes. A refusal
    is logged with the project and the action, never the subject."""

    __slots__ = ("_file", "_log")

    def __init__(self, file: ReloadingFile[_Grants], logger: Logger) -> None:
        self._file = file
        self._log = logger

    @classmethod
    def from_file(cls, path: Path, logger: Logger) -> PrototypeFileProjectAccess:
        """Read the file once at start-up; ``AccessFileError`` when it cannot be read or is
        invalid."""
        file = ReloadingFile(path, parse_access_file, AccessFileError, _WHAT)
        file.load()
        return cls(file, logger)

    async def check_access(self, principal: Principal, project_id: UUID, action: Action) -> None:
        grants = await self._file.current()
        if grants.allows(principal.subject, project_id):
            return
        self._log.info(
            "prototype access file refused project access",
            component="prototype-project-access",
            project_id=project_id,
            action=action,
        )
        raise ProjectAccessDenied("the access file grants no access to the project")


def parse_access_file(raw: bytes) -> _Grants:
    """The grants of the access file (``AccessFileError`` naming the entry, never a value)."""
    doc = json_object(raw, _WHAT, frozenset({"subjects"}), AccessFileError)
    items = doc["subjects"]
    if not isinstance(items, list) or len(items) > MAX_SUBJECTS:
        raise AccessFileError(f"{_WHAT}: subjects must be a list of at most {MAX_SUBJECTS}")
    everything: set[str] = set()
    projects: dict[str, frozenset[UUID]] = {}
    for index, item in enumerate(items):
        where = f"{_WHAT}: entry {index}"
        subject, granted = _grant(where, item)
        if subject in everything or subject in projects:
            raise AccessFileError(f"{where}: subject is listed twice")
        if granted is None:
            everything.add(subject)
        else:
            projects[subject] = granted
    return _Grants(everything=frozenset(everything), projects=projects)


def _grant(where: str, item: Any) -> tuple[str, frozenset[UUID] | None]:
    if not isinstance(item, dict):
        raise AccessFileError(f"{where} is not an object")
    check_keys(item, _GRANT_KEYS, where, AccessFileError)
    subject, granted = item["subject"], item["projects"]
    if not isinstance(subject, str) or not 0 < len(subject) <= MAX_SUBJECT_LEN:
        raise AccessFileError(f"{where}: subject must be 1 to {MAX_SUBJECT_LEN} characters")
    if granted == ALL_PROJECTS:
        return subject, None
    if not isinstance(granted, list) or len(granted) > MAX_PROJECTS_PER_SUBJECT:
        raise AccessFileError(f'{where}: projects must be "*" or a list of project IDs')
    ids: set[UUID] = set()
    for value in granted:
        if not isinstance(value, str):
            raise AccessFileError(f"{where}: projects must hold canonical UUIDs")
        try:
            ids.add(parse_id(value))
        except DomainError:
            raise AccessFileError(f"{where}: projects must hold canonical UUIDs") from None
    return subject, frozenset(ids)
