"""A value that must never reach a log, an error or a response: a database password, a DSN carrying
it, an API token."""

from __future__ import annotations

from typing import Final, NoReturn, final

REDACTED: Final = "[REDACTED]"


@final
class Secret:
    """Prints as ``[REDACTED]`` through ``str``, ``repr``, every format spec, %-formatting,
    logging and ``json.dumps(default=str)``, whether it is set or not, so the output does not even
    tell a set value from an empty one. ``reveal`` is the only way out, called at the one place
    that connects.

    It has no ``__dict__``, cannot be pickled, compares by identity and is unhashable, so neither a
    generic dumper nor an equality probe reaches the value."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        """The secret value. Call it only to connect to the service the value belongs to."""
        return self._value

    def is_set(self) -> bool:
        """Whether the secret holds a non-empty value."""
        return self._value != ""

    def __str__(self) -> str:
        return REDACTED

    def __repr__(self) -> str:
        return REDACTED

    def __format__(self, spec: str) -> str:
        return format(REDACTED, spec)

    def __eq__(self, other: object) -> bool:
        return self is other

    __hash__ = None  # type: ignore[assignment]  # unhashable on purpose: a hash is a probe

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        raise TypeError("a Secret cannot be pickled")

    def __copy__(self) -> Secret:
        return Secret(self._value)

    def __deepcopy__(self, memo: object) -> Secret:
        return Secret(self._value)
