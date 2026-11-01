"""The verified identity a transport hands to every use case."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Principal:
    """The token's subject and the scopes it grants; never the raw token (decision 0004
    section 1). The verifier refuses an expired token, so a principal carries no expiry. The
    empty principal (no subject, no scope) is permitted nothing. ``subject`` is stored as
    requested_by_subject with every deploy, promote and rollback command.

    ``user_id`` is the user's platform ID (decision 0017 section 2): the JWT's ``user_id`` claim
    (a JWT without one is refused, decision 0024), or a development token's ``user_id``; None when
    a development token names none. It is not part of the emptiness rule. The Project
    service's access check needs it: a principal without one has no project access there."""

    subject: str = ""
    scopes: tuple[str, ...] = ()
    user_id: UUID | None = None

    def has_scope(self, scope: str) -> bool:
        """Whether ``scope`` is one of the scopes, compared whole; the empty scope is held by
        nobody."""
        return scope != "" and scope in self.scopes

    def is_empty(self) -> bool:
        """Whether the principal has neither subject nor scope."""
        return self.subject == "" and not self.scopes
