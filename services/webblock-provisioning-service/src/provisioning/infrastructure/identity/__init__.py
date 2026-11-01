"""Identity adapters (decision 0006 section 5, decision 0024): the bearer-JWT verifier on top of
web-block-commons, the bridge of the library's log records, and the action policy. No type here
accepts a token it cannot verify. The prototype's development tokens are a stand-in in
``infrastructure.standins.dev_tokens``."""

from provisioning.infrastructure.identity.log_bridge import attach as attach_library_logs
from provisioning.infrastructure.identity.policy import ActionScopePolicy
from provisioning.infrastructure.identity.verifier import IdentityKeysUnavailable, JwtVerifier

__all__ = ["ActionScopePolicy", "IdentityKeysUnavailable", "JwtVerifier", "attach_library_logs"]
