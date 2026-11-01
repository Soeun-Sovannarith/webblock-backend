"""The prototype's repository registry and locator (decision 0014 section 10.2): a configured JSON
file maps each repository ID to its project, the project's sub-domain (decision 0021 D8) and a Git
URL on the local Git server. Project's registry (OD-01) replaces it. Selected only by
REPOSITORY_MODE=prototype-file.

    {"repositories": [{"repository_id": 4242, "project_id": "<uuid>", "subdomain": "shop",
                       "git_url": "http://webblock-git/site.git"}]}

Each project has at most one registered repository, each repository one project, and each
sub-domain one entry. The format changed with the sub-domain (decision 0021 D3): the key
``github_repository_id`` became ``repository_id``, so a file in the old format is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from uuid import UUID

from provisioning.core.application import DependencyUnavailable, RegisteredRepository
from provisioning.core.domain import DomainError, parse_id, parse_repository_id, validate_subdomain
from provisioning.infrastructure.config.files import (
    ConfigFileError,
    check_keys,
    json_object,
    read_file,
)
from provisioning.infrastructure.config.net import http_url

_WHAT: Final = "repository file"
_ENTRY_KEYS: Final = frozenset({"repository_id", "project_id", "subdomain", "git_url"})
MAX_REPOSITORIES: Final = 10_000


class RepositoryFileError(ConfigFileError):
    """The repository file cannot be read or is invalid; the message holds no value from it."""


@dataclass(frozen=True, slots=True)
class _Repository:
    project_id: UUID
    subdomain: str
    git_url: str


class PrototypeRepositoryFile:
    """Implements RepositoryRegistry (the inbox's check) and RepositoryLocator (the build's clone
    URL) from the configured file."""

    __slots__ = ("_by_id", "_by_project")

    def __init__(self, repositories: dict[int, _Repository]) -> None:
        self._by_id = dict(repositories)
        self._by_project = {r.project_id: rid for rid, r in repositories.items()}

    @classmethod
    def from_file(cls, path: Path) -> PrototypeRepositoryFile:
        try:
            raw = read_file(path, _WHAT)
        except ConfigFileError as err:
            raise RepositoryFileError(str(err)) from None
        return cls.from_json(raw)

    @classmethod
    def from_json(cls, raw: bytes) -> PrototypeRepositoryFile:
        doc = json_object(raw, _WHAT, frozenset({"repositories"}), RepositoryFileError)
        items = doc["repositories"]
        if not isinstance(items, list) or len(items) > MAX_REPOSITORIES:
            raise RepositoryFileError(
                f"{_WHAT}: repositories must be a list of at most {MAX_REPOSITORIES}"
            )
        repositories: dict[int, _Repository] = {}
        projects: set[UUID] = set()
        subdomains: set[str] = set()
        for index, item in enumerate(items):
            repository_id, repository = _entry(index, item)
            if repository_id in repositories:
                raise RepositoryFileError(f"{_WHAT}: entry {index}: repository_id is listed twice")
            if repository.project_id in projects:
                raise RepositoryFileError(
                    f"{_WHAT}: entry {index}: project_id already has a repository"
                )
            if repository.subdomain in subdomains:
                raise RepositoryFileError(f"{_WHAT}: entry {index}: subdomain is listed twice")
            repositories[repository_id] = repository
            projects.add(repository.project_id)
            subdomains.add(repository.subdomain)
        return cls(repositories)

    def count(self) -> int:
        return len(self._by_id)

    async def registered_repository(self, project_id: UUID) -> RegisteredRepository | None:
        """The repository and sub-domain the file registers for the project, or None."""
        repository_id = self._by_project.get(project_id)
        if repository_id is None:
            return None
        return RegisteredRepository(
            repository_id=repository_id, subdomain=self._by_id[repository_id].subdomain
        )

    async def repository_url(self, project_id: UUID, repository_id: int) -> str:
        """The clone URL; ``DependencyUnavailable`` when the file does not hold the repository for
        this project."""
        repository = self._by_id.get(repository_id)
        if repository is None or repository.project_id != project_id:
            raise DependencyUnavailable(
                f"repository {repository_id} of project {project_id} is not in the {_WHAT}"
            )
        return repository.git_url


def _entry(index: int, item: Any) -> tuple[int, _Repository]:
    where = f"{_WHAT}: entry {index}"
    if not isinstance(item, dict):
        raise RepositoryFileError(f"{where} is not an object")
    check_keys(item, _ENTRY_KEYS, where, RepositoryFileError)
    repository_id, project_id, subdomain, git_url = (
        item["repository_id"],
        item["project_id"],
        item["subdomain"],
        item["git_url"],
    )
    if not isinstance(repository_id, int) or isinstance(repository_id, bool):
        raise RepositoryFileError(f"{where}: repository_id must be a positive integer")
    try:
        parse_repository_id(repository_id)
    except DomainError:
        raise RepositoryFileError(f"{where}: repository_id must be a positive integer") from None
    if not isinstance(project_id, str):
        raise RepositoryFileError(f"{where}: project_id must be a canonical UUID")
    try:
        project = parse_id(project_id)
    except DomainError:
        raise RepositoryFileError(f"{where}: project_id must be a canonical UUID") from None
    if not isinstance(subdomain, str):
        raise RepositoryFileError(f"{where}: subdomain must be a DNS label")
    try:
        validate_subdomain(subdomain)
    except DomainError:
        raise RepositoryFileError(f"{where}: subdomain must be a DNS label") from None
    if not isinstance(git_url, str) or not http_url(git_url, schemes=("http", "https")):
        raise RepositoryFileError(
            f"{where}: git_url must be an http or https URL without credentials, query or fragment"
        )
    return repository_id, _Repository(project_id=project, subdomain=subdomain, git_url=git_url)
