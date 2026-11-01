"""The run profile and the keys that only one profile accepts (task H2).

PROVISIONING_PROFILE is required in every process and has no default: ``prototype`` or
``production`` (``core.application.shared.profile.Profile``). In ``production`` every key that
selects or feeds a prototype stand-in is refused by name when it is set (``refuse_in_production``):
real implementations of those contracts do not exist yet (OD-01, OD-04), so the processes fail
closed instead (``hosts.shared.open_contracts``). A key that no profile of a process accepts any
more is refused with ``refuse_everywhere`` (the API's build keys after decision 0016 section 5 item
2), so a stale environment file is caught at start-up instead of silently ignored.

When the profile key itself fails, the sections read the other keys as the prototype does, so one
run still names every problem.
"""

from __future__ import annotations

from typing import Final

from provisioning.core.application.shared.profile import Profile
from provisioning.infrastructure.config.loader import Loader

PROFILE_KEY: Final = "PROVISIONING_PROFILE"


def read_profile(loader: Loader) -> Profile:
    """PROVISIONING_PROFILE; the prototype when the key failed (the load fails anyway)."""
    value = loader.choice(PROFILE_KEY, tuple(p.value for p in Profile))
    return Profile(value) if value else Profile.PROTOTYPE


def refuse_in_production(loader: Loader, profile: Profile, key: str, what: str) -> bool:
    """In production, refuse ``key`` when it is set, naming ``what`` it would select. True when
    the key must not be read (production), whether it was set or not."""
    if profile is not Profile.PRODUCTION:
        return False
    if loader.optional(key) is not None:
        loader.fail(key, f"must be unset in production: {what}")
    return True


def refuse_everywhere(loader: Loader, keys: tuple[str, ...], reason: str) -> None:
    """Refuse each of ``keys`` that is set, with ``reason``."""
    for key in keys:
        if loader.optional(key) is not None:
            loader.fail(key, reason)
