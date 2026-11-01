"""The prototype's repository file, read again whenever it changes (task G; decision 0014 section
10.2). A running build worker or API then sees a project registered after it started, which the
end-to-end tests and the demo need: each registers its own project. Project's registry (OD-01)
replaces it. Selected only by REPOSITORY_MODE=prototype-file.

Each call compares the file's modification time and size with the copy in use and reads it again
when either changed, on a worker thread. A rewrite that cannot be read or is invalid is ignored and
the last good copy stays in use; writers replace the file atomically (write, then rename).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import UUID

from provisioning.core.application import RegisteredRepository
from provisioning.infrastructure.standins.repositories import (
    PrototypeRepositoryFile,
    RepositoryFileError,
)

type _Stamp = tuple[int, int]


class PrototypeReloadingRepositoryFile:
    """Implements ``RepositoryRegistry`` and ``RepositoryLocator`` over the current contents of
    the configured file."""

    __slots__ = ("_current", "_path", "_stamp")

    def __init__(self, path: Path, current: PrototypeRepositoryFile, stamp: _Stamp) -> None:
        self._path = path
        self._current = current
        self._stamp = stamp

    @classmethod
    def from_file(cls, path: Path) -> PrototypeReloadingRepositoryFile:
        """Read the file once; ``RepositoryFileError`` (without its contents) when it cannot be
        read or is invalid at the start."""
        stamp = _stamp_of(path)
        if stamp is None:
            raise RepositoryFileError("repository file cannot be read")
        return cls(path, PrototypeRepositoryFile.from_file(path), stamp)

    async def registered_repository(self, project_id: UUID) -> RegisteredRepository | None:
        await asyncio.to_thread(self._refresh)
        return await self._current.registered_repository(project_id)

    async def repository_url(self, project_id: UUID, repository_id: int) -> str:
        await asyncio.to_thread(self._refresh)
        return await self._current.repository_url(project_id, repository_id)

    def _refresh(self) -> None:
        stamp = _stamp_of(self._path)
        if stamp is None or stamp == self._stamp:
            return
        try:
            current = PrototypeRepositoryFile.from_file(self._path)
        except RepositoryFileError:
            return  # keep the last good copy; a writer replaces the file atomically
        self._current, self._stamp = current, stamp


def _stamp_of(path: Path) -> _Stamp | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size
