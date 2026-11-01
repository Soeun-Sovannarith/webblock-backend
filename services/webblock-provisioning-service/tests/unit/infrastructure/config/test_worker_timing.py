"""QA-06 (task W6-B): the worker refuses timing that no command can survive.

Each key was range-checked alone and nothing related them; ``load_worker`` now refuses each
combination below with a ``LoadError`` that names both (or all) keys of the pair. The QA
reproduction (QA branch ``d487207``,
``tests/qa/test_qa_worker_config_accepts_impossible_timing.py``) runs as written; the
parametrized pairs also cover the similar pairs the review found.
"""

from datetime import timedelta
from pathlib import Path

import pytest
from tests.unit.hosts.envs import build_worker_env, worker_env

from provisioning.infrastructure.config import LoadError
from provisioning.infrastructure.config.build_worker import load_build_worker
from provisioning.infrastructure.config.worker import load_worker

KUBECONFIG = Path("/project/bin/kind-state/proto/worker.kubeconfig")
REPOSITORIES = Path("/project/bin/kind-state/proto/repositories.json")


@pytest.mark.parametrize(
    ("override", "key", "named"),
    [
        (
            {"COMMAND_DEADLINE": "20s", "DEPLOY_FINISH_MARGIN": "30s"},
            "COMMAND_DEADLINE",
            ("DEPLOY_FINISH_MARGIN",),
        ),
        (
            {"WORKER_ATTEMPT_TIMEOUT": "30s", "DEPLOY_READY_TIMEOUT": "90s"},
            "WORKER_ATTEMPT_TIMEOUT",
            ("DEPLOY_READY_TIMEOUT",),
        ),
        (
            {"WORKER_ATTEMPT_TIMEOUT": "100s", "ROUTE_SWITCH_DEADLINE": "60s"},
            "WORKER_ATTEMPT_TIMEOUT",
            ("ROUTE_SWITCH_DEADLINE", "ROUTE_RESTORE_DEADLINE"),
        ),
        # One go-live try may put the route back first, then run its preview and its switch
        # (each up to the switch deadline) and restore after them (TR-58).
        (
            {
                "WORKER_ATTEMPT_TIMEOUT": "150s",
                "ROUTE_SWITCH_DEADLINE": "60s",
                "ROUTE_RESTORE_DEADLINE": "60s",
            },
            "WORKER_ATTEMPT_TIMEOUT",
            ("ROUTE_SWITCH_DEADLINE", "ROUTE_RESTORE_DEADLINE"),
        ),
        (
            {"DEPLOY_FINISH_MARGIN": "10s", "DEPLOY_DELETE_TIMEOUT": "30s"},
            "DEPLOY_FINISH_MARGIN",
            ("DEPLOY_DELETE_TIMEOUT",),
        ),
        (
            {"DEPLOY_ABANDON_TIMEOUT": "10s", "DEPLOY_DELETE_TIMEOUT": "30s"},
            "DEPLOY_ABANDON_TIMEOUT",
            ("DEPLOY_DELETE_TIMEOUT",),
        ),
        (
            {"WORKER_HEARTBEAT_INTERVAL": "15s", "WORKER_LEASE_TTL": "30s"},
            "WORKER_HEARTBEAT_INTERVAL",
            ("WORKER_LEASE_TTL",),
        ),
        (
            {"WORKER_ATTEMPT_TIMEOUT": "40m", "RABBITMQ_CONSUMER_TIMEOUT": "30m"},
            "WORKER_ATTEMPT_TIMEOUT",
            ("RABBITMQ_CONSUMER_TIMEOUT",),
        ),
        (
            {"ROUTE_POLL_INTERVAL": "60s"},
            "ROUTE_POLL_INTERVAL",
            ("ROUTE_SWITCH_DEADLINE", "ROUTE_RESTORE_DEADLINE"),
        ),
        (
            {"DEPLOY_POLL_INTERVAL": "31s"},
            "DEPLOY_POLL_INTERVAL",
            ("DEPLOY_READY_TIMEOUT",),
        ),
    ],
    ids=[
        "deadline-below-margin",
        "attempt-below-ready-wait",
        "attempt-below-switch-wait",
        "attempt-below-a-whole-promotion",
        "margin-below-delete",
        "abandon-below-delete",
        "heartbeat-above-a-third-of-the-ttl",
        "attempt-above-the-broker-timeout",
        "route-poll-not-below-the-deadlines",
        "deploy-poll-above-a-third-of-ready",
    ],
)
def test_the_worker_refuses_timing_that_no_command_can_survive(
    override: dict[str, str], key: str, named: tuple[str, ...]
) -> None:
    env = worker_env(KUBECONFIG) | override
    with pytest.raises(LoadError) as raised:
        load_worker(env.get)
    assert raised.value.keys() == (key,)
    (problem,) = raised.value.problems
    assert all(other in problem.reason for other in named)
    assert not any(value in problem.reason for value in override.values())


@pytest.mark.parametrize(
    "override",
    [
        {"COMMAND_DEADLINE": "20s", "DEPLOY_FINISH_MARGIN": "30s"},
        {"WORKER_ATTEMPT_TIMEOUT": "30s", "DEPLOY_READY_TIMEOUT": "90s"},
        {"WORKER_ATTEMPT_TIMEOUT": "20s", "ROUTE_SWITCH_DEADLINE": "60s"},
    ],
    ids=["deadline-below-margin", "attempt-below-ready-wait", "attempt-below-switch-wait"],
)
def test_the_qa06_reproduction_is_refused(override: dict[str, str]) -> None:
    """The QA reproduction as written (``LoadError`` for each combination)."""
    env = worker_env(KUBECONFIG) | override
    with pytest.raises(LoadError):
        load_worker(env.get)


def test_the_shipped_timing_is_accepted() -> None:
    config = load_worker(worker_env(KUBECONFIG).get)
    assert config.consumer.attempt_timeout > config.deploy.ready_timeout


def test_a_cross_check_does_not_repeat_a_key_already_refused() -> None:
    """A key refused alone is not checked against the other key again (one reason per key)."""
    env = worker_env(KUBECONFIG) | {"DEPLOY_FINISH_MARGIN": "not-a-duration"}
    with pytest.raises(LoadError) as raised:
        load_worker(env.get)
    assert raised.value.keys() == ("DEPLOY_FINISH_MARGIN",)


def test_the_probe_gap_defaults_to_half_a_second() -> None:
    config = load_worker(worker_env(KUBECONFIG).get)
    assert config.site.probe_gap == timedelta(milliseconds=500)


@pytest.mark.parametrize(("value", "gap"), [("0", timedelta(0)), ("10s", timedelta(seconds=10))])
def test_the_probe_gap_accepts_its_bounds(value: str, gap: timedelta) -> None:
    config = load_worker((worker_env(KUBECONFIG) | {"SITE_PROBE_GAP": value}).get)
    assert config.site.probe_gap == gap


@pytest.mark.parametrize("value", ["11s", "-1s", "half", "500"])
def test_the_probe_gap_refuses_values_out_of_bounds(value: str) -> None:
    env = worker_env(KUBECONFIG) | {"SITE_PROBE_GAP": value}
    with pytest.raises(LoadError) as raised:
        load_worker(env.get)
    assert raised.value.keys() == ("SITE_PROBE_GAP",)
    assert value not in str(raised.value)


def test_a_probe_that_cannot_fit_in_the_switch_deadline_is_refused() -> None:
    """Four runs (QA-08) of ten probes ten seconds apart never fit in a 60 s switch."""
    env = worker_env(KUBECONFIG) | {"SITE_PROBE_GAP": "10s", "SITE_PROBE_SUCCESSES": "10"}
    with pytest.raises(LoadError) as raised:
        load_worker(env.get)
    assert raised.value.keys() == ("SITE_PROBE_GAP",)
    (problem,) = raised.value.problems
    assert "SITE_PROBE_SUCCESSES" in problem.reason and "ROUTE_SWITCH_DEADLINE" in problem.reason


@pytest.mark.parametrize("hold_max", ["30m", "1h"], ids=["equal", "above"])
@pytest.mark.parametrize(
    ("process", "key"),
    [("worker", "WORKER_STORE_HOLD_MAX"), ("build_worker", "INBOX_STORE_HOLD_MAX")],
)
def test_hold_max_must_be_below_consumer_timeout(process: str, key: str, hold_max: str) -> None:
    """Decision 0023 G8d: a held delivery must be settled before the broker's consumer timeout
    (30m in the test environment) closes the channel; the reason names both keys, no value."""
    with pytest.raises(LoadError) as raised:
        if process == "worker":
            load_worker((worker_env(KUBECONFIG) | {key: hold_max}).get)
        else:
            load_build_worker((build_worker_env(REPOSITORIES) | {key: hold_max}).get)
    assert raised.value.keys() == (key,)
    (problem,) = raised.value.problems
    assert "RABBITMQ_CONSUMER_TIMEOUT" in problem.reason
    assert hold_max not in problem.reason and "30m" not in problem.reason


# --- TR-58: the timing must hold 3 go-live tries (FR-21, FR-22; decision 0027; design G12) -------

# The prototype's worker timing (scripts/prototype/run-provisioning.sh).
PROTOTYPE = {
    "ROUTE_SWITCH_DEADLINE": "20s",
    "ROUTE_RESTORE_DEADLINE": "60s",
    "WORKER_LEASE_TTL": "15s",
    "WORKER_HEARTBEAT_INTERVAL": "3s",
    "WORKER_RETRY_BACKOFF_BASE": "1s",
    "WORKER_RETRY_BACKOFF_MAX": "10s",
    "COMMAND_DEADLINE": "15m",
    "WORKER_ATTEMPT_TIMEOUT": "10m",
    "WORKER_MAX_ATTEMPTS": "20",
}
COMMAND_KEYS = (
    "ROUTE_SWITCH_DEADLINE",
    "ROUTE_RESTORE_DEADLINE",
    "WORKER_LEASE_TTL",
    "WORKER_RETRY_BACKOFF_MAX",
)


def _refused(override: dict[str, str]) -> tuple[str, str]:
    """The one refused key and its reason, which never holds a value of ``override``."""
    with pytest.raises(LoadError) as raised:
        load_worker((worker_env(KUBECONFIG) | override).get)
    (problem,) = raised.value.problems
    assert not any(value in problem.reason for value in override.values())
    (key,) = raised.value.keys()
    return key, problem.reason


def test_tr58_an_attempt_timeout_below_two_switches_and_two_restores_is_refused() -> None:
    """TR-58: one claim may put the route back first, run the preview and the switch, and
    restore after them: 2 x 60 s + 2 x 60 s = 240 s, so 230 s is refused (the old bound, the
    larger of 2S + R and 2R, was 180 s) and 241 s is accepted."""
    route = {"ROUTE_SWITCH_DEADLINE": "60s", "ROUTE_RESTORE_DEADLINE": "60s"}
    key, reason = _refused(route | {"WORKER_ATTEMPT_TIMEOUT": "230s"})
    assert key == "WORKER_ATTEMPT_TIMEOUT"
    assert "ROUTE_SWITCH_DEADLINE" in reason and "ROUTE_RESTORE_DEADLINE" in reason
    load_worker((worker_env(KUBECONFIG) | route | {"WORKER_ATTEMPT_TIMEOUT": "241s"}).get)


@pytest.mark.parametrize(("deadline", "accepted"), [("615s", False), ("616s", True)])
def test_tr58_a_command_deadline_below_three_tries_and_their_interruptions_is_refused(
    deadline: str, accepted: bool
) -> None:
    """TR-58: 3 x (2 x 20 s + 60 s) for the tries, 30 s + 60 s of waits, and 3 x (15 s + 60 s)
    for an interruption on each try: 615 s. The deadline must be above it."""
    override = PROTOTYPE | {"COMMAND_DEADLINE": deadline}
    if accepted:
        load_worker((worker_env(KUBECONFIG) | override).get)
        return
    key, reason = _refused(override)
    assert key == "COMMAND_DEADLINE"
    assert all(name in reason for name in COMMAND_KEYS)


@pytest.mark.parametrize(
    ("lease", "backoff"), [("15s", "30s"), ("30s", "15s")], ids=["backoff-longer", "lease-longer"]
)
def test_tr58_the_longer_of_lease_and_backoff_counts(lease: str, backoff: str) -> None:
    """TR-58: an interruption costs the longer of the lease and the backoff, plus a restore:
    with 30 s the bound is 3 x 100 s + 90 s + 3 x (30 s + 60 s) = 660 s."""
    timing = PROTOTYPE | {
        "WORKER_LEASE_TTL": lease,
        "WORKER_HEARTBEAT_INTERVAL": "5s",
        "WORKER_RETRY_BACKOFF_MAX": backoff,
    }
    key, _ = _refused(timing | {"COMMAND_DEADLINE": "660s"})
    assert key == "COMMAND_DEADLINE"
    load_worker((worker_env(KUBECONFIG) | timing | {"COMMAND_DEADLINE": "661s"}).get)


def test_tr58_an_attempt_budget_of_three_is_refused() -> None:
    """TR-58: the generic claim budget must leave room for claim 4, which ends a promote whose
    try 3 was interrupted (design G9), so it must be above the go-live tries."""
    key, reason = _refused({"WORKER_MAX_ATTEMPTS": "3"})
    assert key == "WORKER_MAX_ATTEMPTS"
    assert "go-live tries" in reason
    config = load_worker((worker_env(KUBECONFIG) | {"WORKER_MAX_ATTEMPTS": "4"}).get)
    assert config.consumer.max_attempts == 4


def test_tr58_the_prototype_timing_is_accepted() -> None:
    """TR-58: the prototype's values hold 3 tries: 15 m is above 615 s."""
    config = load_worker((worker_env(KUBECONFIG) | PROTOTYPE).get)
    assert config.command_deadline == timedelta(minutes=15)
    assert config.consumer.max_attempts == 20
