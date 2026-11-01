"""Names vulture cannot see being used, read by make qa-sweep (decision 0026 Q5).

vulture reads this file next to src/ and counts every name in it as used. Only names that a
framework or the language calls belong here, each with its caller in a comment. Code that only
tests use is not a false positive and never goes here. Nothing imports or runs this file.
"""

from vulture.whitelist_utils import Whitelist

_ = Whitelist()

_ = (
    # uvicorn.Server reads should_exit in its serve loop and calls capture_signals around it
    # (hosts/api/wiring.py and hosts/shared/ops_listener.py set or override them).
    _.should_exit,
    _.capture_signals,
    # logging calls Handler.handleError when emit fails (telemetry/logger.py, telemetry/sentry.py).
    _.handleError,
    # pydantic reads the class attribute model_config (presentation/messaging/models.py).
    _.model_config,
    # requests.Session reads trust_env (telemetry/tracing.py, the OTLP exporter's session).
    _.trust_env,
    # kubernetes_asyncio's generated models read Configuration.client_side_validation
    # (infrastructure/kubernetes/_client.py).
    _.client_side_validation,
    # Parameters Python's protocols require: __aexit__ (tb), __reduce_ex__ (protocol) and
    # __deepcopy__ (memo).
    _.tb,
    _.protocol,
    _.memo,
)
