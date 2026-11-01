"""Prometheus metrics of one process (task H4; decision 0016 section 5 item 4).

``Metrics`` owns a private ``CollectorRegistry`` (never the library's global one) and every
instrument. It implements the measurement ports of ``core.application.ports.metrics`` (the command
consumer, the build poller, the public HTTP listener), and observes:

- the audit events (``MeteredAuditLog``): a command the worker ended (by kind, status and failure
  code), a build attempt that finished (by outcome and failure code, with its duration when this
  process saw it start), the uncertain-route alert;
- the identity's log events (``observe_log_event``): a refused token by its fixed ``auth_cause``,
  and a failed key fetch;
- the SQL samples of ``infrastructure.telemetry.samples`` (dispatch lag, inbox backlog), read when
  /metrics is scraped (``render``).

Labels are low-cardinality by construction: no label ever carries a project, release, command,
step or subject ID. Every label value passes an allow-list here, and anything outside it becomes
``other``; the failure-code label's allow-list is derived from the failure-code catalogue
(``FAILURE_CODE_LABELS``). The unit test feeds identifiers to every instrument, then scans the
whole registry: every label name must be on the test's own list and no value may look like an
identifier.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any, Final
from uuid import UUID

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
)
from prometheus_client.exposition import CONTENT_TYPE_PLAIN_0_0_4, generate_latest
from prometheus_client.gc_collector import GCCollector
from prometheus_client.process_collector import ProcessCollector

from provisioning.core.application.builds.background.poll_builds import PendingOutcome, PollOutcome
from provisioning.core.application.builds.ports import SweepOutcome
from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditLog,
    AuditResult,
)
from provisioning.core.domain import FAILURE_CODES, OTHER_CODES, CommandKind

OTHER: Final = "other"
"""The value of a label whose input is outside its allow-list."""

CONTENT_TYPE: Final = CONTENT_TYPE_PLAIN_0_0_4
"""The exposition's content type: the Prometheus text format 0.0.4."""

# --- the allow-lists ---------------------------------------------------------------------------

CONSUMER_CODES: Final = frozenset({"transient_failure", "attempt_timeout", "worker_stopping"})
"""The codes of the catalogue's second table (``OTHER_CODES``) that the worker's command consumer
writes on the dispatch row; the failure-code label carries them too. A code here that the
catalogue no longer lists is left out of the label's allow-list."""

FAILURE_CODE_LABELS: Final = frozenset(
    {entry.code for entry in FAILURE_CODES}
    | {entry.code for entry in OTHER_CODES if entry.code in CONSUMER_CODES}
)
"""The failure codes a label may carry (task H4-FU): every code of the failure-code catalogue
(``core.domain.values.failure_codes``) and the consumer's codes of its second table. The catalogue
is a fixed tuple of written codes, so the set is finite and holds no identifier; any other code (a
build system's own that the catalogue does not list, a code added later) counts as ``other``."""

NO_FAILURE: Final = "none"
"""The failure-code label of an outcome without a failure."""

AUTH_CAUSES: Final = frozenset(
    {
        "alg_not_allowed",
        "audience_mismatch",
        "authorization_count",
        "authorization_scheme",
        "bad_signature",
        "claims_malformed",
        "exp_missing",
        "expired",
        "forbidden_header",
        "header_malformed",
        "iat_in_future",
        "internal",
        "issuer_mismatch",
        "keys_unavailable",
        "kid_invalid",
        "lifetime_exceeded",
        "not_yet_valid",
        "sub_invalid",
        "sub_missing",
        "token_format",
        "token_size",
        "typ_mismatch",
        "unknown_kid",
        "user_id_missing",
    }
)
"""The identity's fixed refusal causes: web-block-commons' and Provisioning's own (decision 0024
G5, G6, G8)."""

KEY_FETCH_CAUSES: Final = frozenset(
    {
        "discovery_issuer",
        "discovery_jwks_uri",
        "fetch_encoding",
        "fetch_status",
        "fetch_too_large",
        "jwks_invalid",
        "jwks_timeout",
        "jwks_transport",
    }
)
"""The fixed causes of a failed fetch of the issuer's discovery document or key set
(web-block-commons, decision 0024 G8)."""

COMMAND_KINDS: Final = frozenset(k.value for k in CommandKind)
COMMAND_STATUSES: Final = frozenset({"succeeded", "failed", "skipped"})
EXECUTION_RESULTS: Final = frozenset(
    {
        "finished",
        "permanent_failure",
        "transient_failure",
        "try_failed",
        "attempt_timeout",
        "claim_lost",
        "fenced",
        "worker_stopping",
    }
)
"""How one execution attempt ended (``presentation.messaging.consumers.consumer``)."""

BUILD_OUTCOMES: Final = frozenset({"succeeded", "failed"})
ROUND_NAMES: Final = frozenset({"poll", "pending", "sweep"})
ROUND_FAILED: Final = "round_failed"
ROUND_OUTCOMES: Final = frozenset(
    {o.value for o in PollOutcome}
    | {o.value for o in PendingOutcome}
    | {o.value for o in SweepOutcome}
    | {"error", ROUND_FAILED}
)
METHODS: Final = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
SAMPLES: Final = frozenset({"dispatch", "inbox"})

# --- the buckets -------------------------------------------------------------------------------

_COMMAND_BUCKETS: Final = (0.1, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120, 300, 600)
_BUILD_BUCKETS: Final = (10, 30, 60, 120, 180, 300, 600, 900, 1200, 1800, 3600)
_HTTP_BUCKETS: Final = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)

_COMMAND_ACTIONS: Final = {
    AuditAction.DEPLOY: CommandKind.DEPLOY.value,
    AuditAction.PROMOTE: CommandKind.PROMOTE.value,
    AuditAction.ROLLBACK: CommandKind.ROLLBACK.value,
    AuditAction.SUSPEND_HOSTING: CommandKind.SUSPEND_HOSTING.value,
    AuditAction.RESUME_HOSTING: CommandKind.RESUME_HOSTING.value,
}
_BUILD_STARTED_RESULTS: Final = frozenset({AuditResult.SUCCEEDED, AuditResult.ACCEPTED})
_MAX_STARTS: Final = 4096


def bounded(value: object, allowed: frozenset[str]) -> str:
    """``value`` when it is one of ``allowed``, else ``other``."""
    return value if isinstance(value, str) and value in allowed else OTHER


def status_class(status: int | None) -> str:
    """ "2xx" for 200 to 299 and so on; "none" when no response started."""
    if status is None:
        return "none"
    if 100 <= status <= 599:
        return f"{status // 100}xx"
    return OTHER


type Sample = Callable[[], Awaitable[None]]
"""Reads one SQL sample into its gauges; raises when it could not."""


class Metrics:
    """One process's registry and instruments. ``routes`` is the allow-list of the HTTP route
    label (the public listener's templates); ``sample_timeout`` bounds the SQL samples of one
    scrape. Build one per process; it opens nothing."""

    def __init__(
        self,
        *,
        routes: Iterable[str] = (),
        sample_timeout: float = 5.0,
        process_collectors: bool = True,
    ) -> None:
        if sample_timeout <= 0:
            raise ValueError("metrics: the sample timeout must be positive")
        self.registry = CollectorRegistry(auto_describe=True)
        if process_collectors:
            ProcessCollector(registry=self.registry)
            GCCollector(registry=self.registry)
        self._routes = frozenset(routes)
        self._sample_timeout = sample_timeout
        self._samples: list[tuple[str, Sample, Callable[[], None]]] = []
        self._sample_lock = asyncio.Lock()
        self._build_starts: OrderedDict[UUID, float] = OrderedDict()
        r = self.registry
        self._commands_finished = Counter(
            "provisioning_commands_finished",
            "Commands the worker ended, by kind, status and failure code.",
            ("kind", "status", "failure_code"),
            registry=r,
        )
        self._command_execution = Histogram(
            "provisioning_command_execution_seconds",
            "One execution attempt of a claimed command, by kind and how it ended (deploy and "
            "promote durations are the kind=deploy and kind=promote series).",
            ("kind", "result"),
            buckets=_COMMAND_BUCKETS,
            registry=r,
        )
        self._builds_finished = Counter(
            "provisioning_build_attempts_finished",
            "Build attempts that finished, by outcome and failure code.",
            ("outcome", "failure_code"),
            registry=r,
        )
        self._build_duration = Histogram(
            "provisioning_build_attempt_duration_seconds",
            "From a build attempt's start to its end, when this process saw both, by outcome.",
            ("outcome",),
            buckets=_BUILD_BUCKETS,
            registry=r,
        )
        self._build_rounds = Counter(
            "provisioning_build_round_results",
            "Results of the build worker's poll, pending-start and sweep rounds, by round and "
            "outcome (outcome=unavailable: the build system could not be reached).",
            ("round", "outcome"),
            registry=r,
        )
        self._route_alerts = Counter(
            "provisioning_route_uncertain_alerts",
            "Times a project's route was left uncertain (the route_uncertain audit alert).",
            registry=r,
        )
        self._http_requests = Counter(
            "provisioning_http_requests",
            "Requests on the public listener, by route template, method and status class.",
            ("route", "method", "status_class"),
            registry=r,
        )
        self._http_duration = Histogram(
            "provisioning_http_request_duration_seconds",
            "Duration of a request on the public listener, by route template and method.",
            ("route", "method"),
            buckets=_HTTP_BUCKETS,
            registry=r,
        )
        self._identity_refusals = Counter(
            "provisioning_identity_refusals",
            "Bearer tokens the identity refused, by the fixed cause (auth_cause).",
            ("auth_cause",),
            registry=r,
        )
        self._key_fetch_failures = Counter(
            "provisioning_identity_key_fetch_failures",
            "Failed fetches of the issuer's key set, by the fixed cause (auth_cause).",
            ("auth_cause",),
            registry=r,
        )
        self._sample_failures = Counter(
            "provisioning_metrics_sample_failures",
            "SQL samples that could not be read during a scrape, by sample.",
            ("sample",),
            registry=r,
        )
        self._tenant_rounds = Counter(
            "provisioning_bootstrap_rounds",
            "Bootstrap rounds, by result.",
            ("result",),
            registry=r,
        )
        self._tenants_pending: Gauge | None = None  # registered by the first bootstrap round
        self._standby: Gauge | None = None  # registered by the bootstrap's first lock report

    # --- the ports ------------------------------------------------------------------------------

    def command_executed(self, kind: str, result: str, seconds: float) -> None:
        self._command_execution.labels(
            bounded(kind, COMMAND_KINDS), bounded(result, EXECUTION_RESULTS)
        ).observe(_seconds(seconds))

    def build_round_result(self, round_name: str, outcome: str) -> None:
        self._build_rounds.labels(
            bounded(round_name, ROUND_NAMES), bounded(outcome, ROUND_OUTCOMES)
        ).inc()

    def http_request(self, route: str, method: str, status: int | None, seconds: float) -> None:
        route_label = bounded(route, self._routes)
        method_label = bounded(method, METHODS)
        self._http_requests.labels(route_label, method_label, status_class(status)).inc()
        self._http_duration.labels(route_label, method_label).observe(_seconds(seconds))

    # --- observed events ------------------------------------------------------------------------

    def command_finished(self, kind: str, status: str, failure_code: str | None) -> None:
        self._commands_finished.labels(
            bounded(kind, COMMAND_KINDS),
            bounded(status, COMMAND_STATUSES),
            _failure(failure_code),
        ).inc()

    def build_attempt_started(self, release_id: UUID, at: float) -> None:
        """Remember when this process saw the release's current attempt start (monotonic); the
        first start of an attempt wins, and at most a bounded number are remembered."""
        if release_id in self._build_starts:
            return
        self._build_starts[release_id] = at
        while len(self._build_starts) > _MAX_STARTS:
            self._build_starts.popitem(last=False)

    def build_attempt_finished(
        self, release_id: UUID, outcome: str, failure_code: str | None, at: float
    ) -> None:
        label = bounded(outcome, BUILD_OUTCOMES)
        self._builds_finished.labels(label, _failure(failure_code)).inc()
        started = self._build_starts.pop(release_id, None)
        if started is not None:
            self._build_duration.labels(label).observe(_seconds(at - started))

    def route_uncertain(self) -> None:
        self._route_alerts.inc()

    def bootstrap_round(self, *, not_ready: int | None) -> None:
        """A bootstrap round completed with ``not_ready`` tenants left, or failed (None)."""
        if not_ready is None:
            self._tenant_rounds.labels("failed").inc()
            return
        self._tenant_rounds.labels("completed").inc()
        if self._tenants_pending is None:
            self._tenants_pending = self.gauge(
                "provisioning_bootstrap_tenants_not_ready",
                "Tenants the last completed bootstrap round left not ready.",
            )
        self._tenants_pending.set(max(0, not_ready))

    def bootstrap_standby(self, *, standby: bool) -> None:
        """Task H5c: 1 while this bootstrap waits for the singleton lock another one holds, 0 once
        it holds the lock (registered on the first report, so only in the bootstrap process)."""
        if self._standby is None:
            self._standby = self.gauge(
                "provisioning_bootstrap_standby",
                "1 while this bootstrap stands by because another one holds the singleton lock.",
            )
        self._standby.set(1 if standby else 0)

    def observe_log_event(self, level: str, event: Mapping[str, Any]) -> None:
        """A structured log event before its fields are filtered: the identity's refusals and
        failed key fetches carry a fixed ``auth_cause``. Any other event is ignored."""
        del level
        if event.get("component") != "identity":
            return
        cause = event.get("auth_cause")
        if cause in KEY_FETCH_CAUSES:
            self._key_fetch_failures.labels(cause).inc()
        elif cause is not None:
            self._identity_refusals.labels(bounded(cause, AUTH_CAUSES)).inc()

    # --- the SQL samples and the exposition ------------------------------------------------------

    def gauge(self, name: str, documentation: str, labels: tuple[str, ...] = ()) -> Gauge:
        """A gauge of a SQL sample, registered in this registry."""
        return Gauge(name, documentation, labels, registry=self.registry)

    def add_sample(self, name: str, sample: Sample, unknown: Callable[[], None]) -> None:
        """Read ``sample`` on every scrape; when it fails or times out, count the failure and
        call ``unknown`` (it sets the sample's gauges to NaN, so a stale value is never shown as
        current)."""
        if name not in SAMPLES:
            raise ValueError("metrics: unknown sample name")
        self._samples.append((name, sample, unknown))

    async def render(self) -> tuple[bytes, str]:
        """Read the SQL samples (one scrape at a time, each bounded by the sample timeout), then
        the text exposition and its content type."""
        async with self._sample_lock:
            for name, sample, unknown in self._samples:
                try:
                    async with asyncio.timeout(self._sample_timeout):
                        await sample()
                except Exception:  # counted; the exposition is still served
                    self._sample_failures.labels(name).inc()
                    unknown()
        return generate_latest(self.registry), CONTENT_TYPE


class MeteredAuditLog:
    """An ``AuditLog`` that writes to ``inner`` and then counts the events the metrics observe.
    Like every ``AuditLog``, it never raises."""

    __slots__ = ("_clock", "_inner", "_metrics")

    def __init__(
        self,
        inner: AuditLog,
        metrics: Metrics,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._metrics = metrics
        self._clock = clock

    def record(self, event: AuditEvent) -> None:
        self._inner.record(event)
        self._observe(event)

    def alert(self, event: AuditEvent) -> None:
        self._inner.alert(event)
        with contextlib.suppress(Exception):  # a metric never changes an outcome
            if event.action is AuditAction.ROUTE_UNCERTAIN:
                self._metrics.route_uncertain()

    def _observe(self, event: AuditEvent) -> None:
        with contextlib.suppress(Exception):  # a metric never changes an outcome
            self._count(event)

    def _count(self, event: AuditEvent) -> None:
        if event.actor_kind is not AuditActorKind.SYSTEM:
            return
        kind = _COMMAND_ACTIONS.get(event.action)
        if kind is not None:
            if event.result.value in COMMAND_STATUSES:
                self._metrics.command_finished(kind, event.result.value, event.failure_code)
            return
        if event.release_id is None:
            return
        if event.action is AuditAction.BUILD_ATTEMPT_STARTED:
            if event.result in _BUILD_STARTED_RESULTS:
                self._metrics.build_attempt_started(event.release_id, self._clock())
        elif event.action is AuditAction.BUILD_ATTEMPT_FINISHED:
            self._metrics.build_attempt_finished(
                event.release_id, event.result.value, event.failure_code, self._clock()
            )


def _failure(code: str | None) -> str:
    return NO_FAILURE if not code else bounded(code, FAILURE_CODE_LABELS)


def _seconds(value: float) -> float:
    return value if math.isfinite(value) and value >= 0 else 0.0
