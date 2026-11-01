"""Promote with automatic route restore, and operator rollback (task F2; decision 0001 sections 3
and 7, decision 0014 sections 3 and 4). A promote makes up to 3 go-live tries, one per claim
(decision 0027): a test of how a promotion ends runs two failed tries first.

The fakes (``test_routing_support``) apply the real domain outcome functions and the real
acceptance decision; the use case is the real ``Routing``.
"""

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from tests.unit.core.application.routing.background.test_routing_support import (
    CANDIDATE,
    HOSTNAME,
    LIVE,
    OFFLINE,
    OTHER_BUILT,
    PREVIEW_HOSTNAME,
    PROJECT,
    SITE_DOMAIN,
    SUBDOMAIN,
    FakeCluster,
    FakeEntitlement,
    FakeHostingEntitlement,
    Route,
    World,
    make_release,
    run_failed_tries,
    settings,
    to_release,
    unavailable,
)

from provisioning.core.application import (
    FAILURE_CODE_TRANSIENT,
    Claim,
    ClaimLost,
    PermanentFailure,
    RetryLater,
)
from provisioning.core.application.hosting.background.hosting_job import Hosting
from provisioning.core.application.ports.site_cluster import (
    ApplyConflict,
    ClusterRefused,
    ClusterUnavailable,
    FenceRejected,
    RouteTarget,
    TenantNotReady,
)
from provisioning.core.application.routing.background.routing import (
    CODE_ENTITLEMENT_LOST,
    CODE_OPERATOR_ROLLBACK,
    CODE_PROMOTION_INTERRUPTED,
    CODE_ROUTE_RESTORE_UNCONFIRMED,
    PROBE_CONFIRMATIONS,
    PointResult,
    RoutePointer,
    Routing,
    RoutingSettings,
    preview_hostname,
    site_hostname,
)
from provisioning.core.domain import (
    CODE_ATTEMPTS_EXHAUSTED,
    CODE_CANDIDATE_NOT_ANSWERING,
    CODE_DEPENDENCY_UNAVAILABLE,
    CODE_ROUTE_NOT_ACCEPTED,
    CODE_ROUTE_REFUSED,
    CODE_SITE_NOT_ANSWERING,
    GO_LIVE_TRIES,
    Command,
    CommandKind,
    CommandStatus,
    HostingStatus,
    IllegalTransition,
    ReleaseStatus,
    RouteNotStable,
    RoutingStatus,
    StaleFence,
    check_command_preconditions,
    subdomain_refusal,
)


class Setup:
    """A world with a live release and a ready candidate, a cluster serving both, the candidate
    answering, and a promote command accepted and claimed."""

    def __init__(self, *, live: bool = True) -> None:
        self.log: list[str] = []
        self.world = World(self.log, live=live)
        self.cluster = FakeCluster(self.log, serving={LIVE, CANDIDATE}, answering={CANDIDATE})
        if live:
            # The route of the live release, left by an earlier command.
            self.cluster.route = Route(to_release(LIVE), 0)
        self.entitlement = FakeEntitlement(self.log)
        self.routing = Routing(
            store=self.world,
            cluster=self.cluster,
            entitlement=self.entitlement,
            clock=self.world.clock,
            settings=settings(),
        )
        self.command = self.world.accept(CommandKind.PROMOTE, CANDIDATE)

    def claim(self) -> Claim:
        return self.world.claim(self.command)

    async def fail_tries(self, tries: int = 2) -> list[RetryLater]:
        """Run ``tries`` claims whose tries fail between tries (try 3 is then the last)."""
        return await run_failed_tries(self.routing, self.world, self.command, tries)

    def site_stops_answering(self) -> None:
        """The candidate answers on its preview, but no longer once the site's route moved."""
        self.cluster.preview_answering = {CANDIDATE}
        self.cluster.answering = set()

    def status(self) -> CommandStatus:
        return self.world.command(self.command.id).status

    def failure(self) -> str | None:
        return self.world.command(self.command.id).failure_code


# --- settings -------------------------------------------------------------------------------


def test_site_hostname_is_the_subdomain_under_the_site_domain() -> None:
    """Decision 0023 G18: ``<subdomain>.<site domain>``, from the hosting row's copy."""
    assert site_hostname(SUBDOMAIN, SITE_DOMAIN) == HOSTNAME == f"shop.{SITE_DOMAIN}"
    assert str(PROJECT) not in HOSTNAME


def test_fr20_1_the_preview_hostname_is_the_subdomain_dash_preview_under_the_site_domain() -> None:
    """FR-20.1 (PREVIEW1): ``<subdomain>-preview.<site domain>``, one label under the site domain;
    the site's own host stays ``<subdomain>.<site domain>``."""
    assert preview_hostname(SUBDOMAIN, SITE_DOMAIN) == f"shop-preview.{SITE_DOMAIN}"
    assert PREVIEW_HOSTNAME == preview_hostname(SUBDOMAIN, SITE_DOMAIN)
    assert preview_hostname(SUBDOMAIN, SITE_DOMAIN) != site_hostname(SUBDOMAIN, SITE_DOMAIN)


def test_fr20_the_older_preview_hosts_are_never_built() -> None:
    """FR-20 (PREVIEW1 replaces decision 0023 G18b's host): neither ``<subdomain>.preview.<site
    domain>`` nor the first ``preview-<subdomain>`` host."""
    host = preview_hostname(SUBDOMAIN, SITE_DOMAIN)
    assert host != f"{SUBDOMAIN}.preview.{SITE_DOMAIN}"
    assert host != f"preview-{SUBDOMAIN}.{SITE_DOMAIN}"
    assert host.split(".", 1)[1] == SITE_DOMAIN


_ONE_DNS_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")


def test_fr20_a_55_character_subdomain_gives_a_valid_preview_host() -> None:
    """FR-20: the longest sub-domain the platform hosts has 55 characters; its preview host
    is still a DNS name, every label at most 63 characters."""
    longest = "a" * 54 + "z"
    host = preview_hostname(longest, SITE_DOMAIN)
    assert host == f"{longest}-preview.{SITE_DOMAIN}"
    assert len(host) <= 253
    assert all(_ONE_DNS_LABEL.fullmatch(label) for label in host.split("."))


_HOSTED = ["shop", "preview-shop", "preview", "shop-previews", "a" * 55]


@pytest.mark.parametrize("subdomain", _HOSTED)
def test_fr55_a_preview_host_is_never_the_site_host_of_a_hosted_subdomain(subdomain: str) -> None:
    """FR-55 (PREVIEW1): among the sub-domains the bootstrap gives a tenant, no project's preview
    host equals any site host, ``preview``, ``preview-shop`` and ``shop-previews`` included."""
    preview = preview_hostname(subdomain, SITE_DOMAIN)
    assert all(preview != site_hostname(other, SITE_DOMAIN) for other in _HOSTED)


def test_fr55_the_one_collision_is_a_subdomain_the_bootstrap_refuses() -> None:
    """FR-55.2: ``shop``'s preview host is the site host of ``shop-preview``, which never gets a
    tenant, so no route of it is ever admitted."""
    assert preview_hostname("shop", SITE_DOMAIN) == site_hostname("shop-preview", SITE_DOMAIN)
    assert subdomain_refusal("shop-preview") is not None


# Security review S5: a site domain of at most 181 characters keeps every host the worker builds
# within 253 characters, even for a sub-domain of 63 characters the database allows and FR-55
# refuses: ``<63 characters>-preview.<site domain>`` is 63 + 9 = 72 characters plus the site domain.
LONGEST_SITE_DOMAIN = "a" * 63 + "." + "b" * 63 + "." + "c" * 53


def test_s5_a_site_domain_of_181_characters_is_the_longest_allowed() -> None:
    """Security review S5: 181 characters is accepted and every host of the longest sub-domain is
    at most 253 characters; 182 is refused, naming the field."""
    assert len(LONGEST_SITE_DOMAIN) == 181
    fields = {
        "switch_deadline": timedelta(seconds=1),
        "restore_deadline": timedelta(seconds=1),
        "poll_interval": timedelta(milliseconds=100),
    }
    RoutingSettings(site_domain=LONGEST_SITE_DOMAIN, **fields).validate()
    longest = "z" * 63
    assert len(preview_hostname(longest, LONGEST_SITE_DOMAIN)) == 253
    assert len(site_hostname(longest, LONGEST_SITE_DOMAIN)) <= 253
    with pytest.raises(ValueError, match="site_domain"):
        RoutingSettings(site_domain=LONGEST_SITE_DOMAIN + "c", **fields).validate()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("site_domain", ""),
        ("site_domain", "Sites.webblock.test"),
        ("site_domain", ".sites.webblock.test"),
        ("site_domain", "sites..test"),
        ("site_domain", "*.webblock.test"),
        ("switch_deadline", timedelta(0)),
        ("restore_deadline", timedelta(seconds=-1)),
        ("poll_interval", timedelta(0)),
        ("poll_interval", timedelta(seconds=1)),
    ],
)
def test_settings_refuse_bad_values_naming_the_field(field: str, value: object) -> None:
    good = RoutingSettings(
        site_domain=SITE_DOMAIN,
        switch_deadline=timedelta(seconds=1),
        restore_deadline=timedelta(seconds=1),
        poll_interval=timedelta(milliseconds=100),
    )
    fields: dict[str, object] = {
        "site_domain": good.site_domain,
        "switch_deadline": good.switch_deadline,
        "restore_deadline": good.restore_deadline,
        "poll_interval": good.poll_interval,
    }
    fields[field] = value
    bad = RoutingSettings(**fields)  # type: ignore[arg-type]  # a parametrized field of either type
    good.validate()
    with pytest.raises(ValueError, match=field) as raised:
        bad.validate()
    assert str(value) not in str(raised.value) or str(value) == ""


def test_routing_refuses_invalid_settings_at_construction() -> None:
    log: list[str] = []
    world = World(log)
    with pytest.raises(ValueError, match="poll_interval"):
        Routing(
            store=world,
            cluster=FakeCluster(log),
            entitlement=FakeEntitlement(log),
            clock=world.clock,
            settings=settings(switch=0.1, restore=0.1, interval=0.5),
        )
    assert log == []


# --- promote: success -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promote_supersedes_the_old_live_release_before_the_candidate_goes_live() -> None:
    """FR-19.1: promote supersedes the old live release before the candidate goes live."""
    s = Setup()
    await s.routing.promote(s.claim())

    candidate = s.world.release(CANDIDATE)
    assert candidate.status is ReleaseStatus.LIVE
    assert candidate.promoted_at is not None
    assert s.world.release(LIVE).status is ReleaseStatus.SUPERSEDED
    assert s.world.hosting.live_release_id == CANDIDATE
    assert s.world.routing() is RoutingStatus.STABLE
    assert s.status() is CommandStatus.SUCCEEDED
    # The one-live index: the old live release is written first.
    assert s.world.writes.index(f"release {LIVE} superseded") < s.world.writes.index(
        f"release {CANDIDATE} live"
    )
    # The route points at the candidate, at the command's number, for the site's hostname.
    assert s.cluster.route is not None
    assert s.cluster.route.target == to_release(CANDIDATE)
    assert s.cluster.route.command_number == s.command.command_number
    assert s.cluster.hostnames == (HOSTNAME,)


@pytest.mark.asyncio
async def test_promote_records_the_intent_before_touching_the_route() -> None:
    s = Setup()
    await s.routing.promote(s.claim())

    assert s.world.writes[:2] == [
        f"release {CANDIDATE} promoting",
        "hosting routing=changing hosting=online live=" + str(LIVE),
    ]
    first_apply = next(i for i, line in enumerate(s.log) if line.startswith("apply_route"))
    first_write = s.log.index("store apply")
    assert first_write < first_apply
    # Entitlement is rechecked before the intent is recorded.
    assert s.log.index(f"entitlement {CommandKind.PROMOTE}") < first_write


@pytest.mark.asyncio
async def test_a_first_publish_goes_live_without_superseding() -> None:
    s = Setup(live=False)
    await s.routing.promote(s.claim())

    assert s.world.release(CANDIDATE).status is ReleaseStatus.LIVE
    assert s.world.hosting.live_release_id == CANDIDATE
    assert not any("superseded" in w for w in s.world.writes)
    assert s.status() is CommandStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_promote_waits_for_the_route_to_be_accepted_and_the_site_to_answer() -> None:
    s = Setup()
    s.cluster.fail_status_once = [unavailable()]
    probes = 0

    s.cluster.preview_answering = {CANDIDATE}

    def answer_on_third_probe(hostname: str) -> None:
        nonlocal probes
        if hostname == HOSTNAME:
            probes += 1
            s.cluster.answering = {CANDIDATE} if probes >= 3 else set()

    s.cluster.on_probe = answer_on_third_probe
    await s.routing.promote(s.claim())

    # Two failed runs before the first answer, then the answer and its confirmations (QA-08).
    assert probes == 3 + PROBE_CONFIRMATIONS
    assert s.world.release(CANDIDATE).status is ReleaseStatus.LIVE


# --- promote: the preview and the release header (decision 0014 section 17) ---------------------


@pytest.mark.asyncio
async def test_fr52_1_only_the_preview_probe_names_the_projects_subdomain() -> None:
    """FR-52.1 (security review L1): the promotion asks for the token only on its own preview host,
    by naming the project's sub-domain; the site probe names none, so it never carries the token."""
    s = Setup()
    await s.routing.promote(s.claim())
    previews = [named for host, named in s.cluster.probed_as if host == PREVIEW_HOSTNAME]
    sites = [named for host, named in s.cluster.probed_as if host == HOSTNAME]
    assert previews and set(previews) == {SUBDOMAIN}
    assert sites and set(sites) == {None}
    assert {host for host, _ in s.cluster.probed_as} == {PREVIEW_HOSTNAME, HOSTNAME}


# --- task H5a: a candidate whose readiness reports a dependency down -----------------------------


@pytest.mark.asyncio
async def test_a_candidate_answering_503_fails_with_dependency_unavailable() -> None:
    """The preview probe raises SiteDependencyUnavailable (the candidate answered 503 naming
    itself): the code is dependency_unavailable, and the outcome is that of any failed preview.
    FR-21.6, FR-21.1: after three tries the release is rolled_back with it and the live release
    keeps serving; the site's route never pointed at the candidate."""
    s = Setup()
    s.cluster.outage = {CANDIDATE}
    raised = await s.fail_tries()
    assert [r.code for r in raised] == [CODE_DEPENDENCY_UNAVAILABLE] * 2
    s.log.clear()

    await s.routing.promote(s.claim())

    candidate = s.world.release(CANDIDATE)
    assert (candidate.status, candidate.failure_code) == (
        ReleaseStatus.ROLLED_BACK,
        CODE_DEPENDENCY_UNAVAILABLE,
    )
    assert s.failure() == CODE_DEPENDENCY_UNAVAILABLE
    assert s.world.routing() is RoutingStatus.STABLE
    assert s.cluster.applied_targets() == [
        f"apply_route release {LIVE} #{s.command.command_number}"
    ]
    assert s.world.hosting.live_release_id == LIVE
    assert s.cluster.site_probes() == []
    assert len(s.cluster.preview_probes()) >= 2  # probed until the deadline


def _after_probes(s: Setup, count: int, then: Callable[[], None]) -> None:
    """Run ``then`` before every probe after the first ``count``."""
    seen = 0

    def probe(hostname: str) -> None:
        nonlocal seen
        seen += 1
        if seen > count:
            then()

    s.cluster.on_probe = probe


@pytest.mark.asyncio
async def test_the_last_failed_run_decides_outage_then_silence() -> None:
    s = Setup()
    s.cluster.outage = {CANDIDATE}
    s.cluster.answering = {LIVE}
    _after_probes(s, 1, lambda: s.cluster.outage.clear())
    with pytest.raises(RetryLater) as raised:
        await s.routing.promote(s.claim())
    assert raised.value.code == CODE_CANDIDATE_NOT_ANSWERING


@pytest.mark.asyncio
async def test_the_last_failed_run_decides_silence_then_outage() -> None:
    s = Setup()
    s.cluster.answering = {LIVE}
    _after_probes(s, 1, lambda: s.cluster.outage.add(CANDIDATE))
    with pytest.raises(RetryLater) as raised:
        await s.routing.promote(s.claim())
    assert raised.value.code == "dependency_unavailable"


@pytest.mark.asyncio
async def test_an_unreachable_api_server_keeps_the_last_outage() -> None:
    s = Setup()
    s.cluster.outage = {CANDIDATE}

    def unavailable() -> None:
        raise ClusterUnavailable("503")

    _after_probes(s, 1, unavailable)
    with pytest.raises(RetryLater) as raised:
        await s.routing.promote(s.claim())
    assert raised.value.code == "dependency_unavailable"


@pytest.mark.asyncio
async def test_a_site_answering_503_after_the_switch_keeps_site_not_answering() -> None:
    """Only the preview names the outage; after the switch the restore's code is unchanged (try 3
    shown, FR-21.1)."""
    s = Setup()

    def down_on_the_site_only(hostname: str) -> None:
        if hostname == HOSTNAME:
            s.cluster.outage.add(CANDIDATE)
        else:
            s.cluster.outage.discard(CANDIDATE)

    s.cluster.on_probe = down_on_the_site_only
    raised = await s.fail_tries()
    assert [r.code for r in raised] == [CODE_SITE_NOT_ANSWERING] * 2
    await s.routing.promote(s.claim())

    assert s.failure() == CODE_SITE_NOT_ANSWERING
    candidate = s.world.release(CANDIDATE)
    assert (candidate.status, candidate.failure_code) == (
        ReleaseStatus.ROLLED_BACK,
        CODE_SITE_NOT_ANSWERING,
    )
    assert s.cluster.route == Route(to_release(LIVE), s.command.command_number)
    assert s.world.routing() is RoutingStatus.STABLE


@pytest.mark.asyncio
async def test_a_resumed_claim_whose_preview_answers_503_restores_with_the_outage() -> None:
    """FR-21.5: try 1 was killed after its switch; tries 2 and 3 find the candidate down on its
    preview, and try 3 restores the route with the outage's code."""
    s = Setup()
    await _interrupt_mid_switch(s)

    s.cluster.outage = {CANDIDATE}
    raised = await s.fail_tries(1)
    assert (raised[0].code, raised[0].after) == (CODE_DEPENDENCY_UNAVAILABLE, SIXTY)
    await s.routing.promote(s.claim())

    assert s.cluster.route == Route(to_release(LIVE), s.command.command_number)
    candidate = s.world.release(CANDIDATE)
    assert (candidate.status, candidate.failure_code) == (
        ReleaseStatus.ROLLED_BACK,
        "dependency_unavailable",
    )
    assert s.failure() == "dependency_unavailable"
    assert s.world.routing() is RoutingStatus.STABLE


@pytest.mark.parametrize(
    "refusal",
    [ClusterRefused("422"), ApplyConflict("409"), FenceRejected("newer")],
    ids=["invalid", "conflict", "fenced"],
)
@pytest.mark.asyncio
async def test_fr19_a_refused_preview_is_a_failed_try_with_route_refused(
    refusal: Exception,
) -> None:
    """FR-19, FR-21.6: a refused preview route is a failed try; the site's route is untouched."""
    s = Setup()
    s.cluster.fail_preview = refusal
    with pytest.raises(RetryLater) as raised:
        await s.routing.promote(s.claim())
    assert (raised.value.code, raised.value.after) == (CODE_ROUTE_REFUSED, THIRTY)
    assert s.world.writes == INTENT
    assert s.cluster.applied_targets() == []


@pytest.mark.asyncio
async def test_fr19_2_a_preview_never_confirmed_is_a_failed_try_with_route_not_accepted() -> None:
    """FR-19.2, FR-21.6: a preview route not accepted in time is a failed try with
    route_not_accepted; nothing is probed and the site's route is untouched."""
    s = Setup()
    s.cluster.serving = {LIVE}  # the candidate's Service never resolves on the preview route
    with pytest.raises(RetryLater) as raised:
        await s.routing.promote(s.claim())
    assert (raised.value.code, raised.value.after) == (CODE_ROUTE_NOT_ACCEPTED, THIRTY)
    assert s.world.writes == INTENT
    assert s.cluster.applied_targets() == []
    assert s.cluster.preview_probes() == []


@pytest.mark.asyncio
async def test_the_old_backend_still_answering_after_the_switch_is_not_accepted() -> None:
    """Finding M1: for a moment after the route is accepted the Gateway still sends the site's
    hostname to the live release, which answers 200. Its header names the live release, so the
    promotion waits until the candidate answers."""
    s = Setup()
    s.cluster.answering = {LIVE, CANDIDATE}
    s.cluster.stale_site_probes = 3
    await s.routing.promote(s.claim())

    assert s.status() is CommandStatus.SUCCEEDED
    site = s.cluster.site_probes()
    # Three answered by the live release, then the candidate and its confirmations (QA-08).
    assert len(site) == 4 + PROBE_CONFIRMATIONS
    assert all(x.endswith(f"expect release {CANDIDATE}") for x in site)


@pytest.mark.asyncio
async def test_a_site_answered_only_by_the_old_backend_is_restored() -> None:
    s = Setup()
    s.cluster.answering = {LIVE, CANDIDATE}
    s.cluster.stale_site_probes = 10**9  # the old backend keeps answering until the deadline
    await s.fail_tries()
    await s.routing.promote(s.claim())

    assert s.failure() == CODE_SITE_NOT_ANSWERING
    assert s.world.release(CANDIDATE).status is ReleaseStatus.ROLLED_BACK
    assert s.cluster.route == Route(to_release(LIVE), s.command.command_number)


@pytest.mark.asyncio
async def test_a_resumed_claim_runs_the_preview_again_before_repeating_the_switch() -> None:
    s = Setup()
    await _interrupt_mid_switch(s)

    s.log.clear()
    await s.routing.promote(s.world.claim(s.command))

    number = s.command.command_number
    assert s.log.index(f"apply_preview release {CANDIDATE} #{number}") < s.log.index(
        f"apply_route release {CANDIDATE} #{number}"
    )
    assert s.cluster.preview_probes()
    assert s.status() is CommandStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_a_resumed_claim_whose_preview_fails_restores_the_route() -> None:
    """The release is already promoting and the route points at the candidate after try 1 was
    killed: the next claims put the route back first, and a candidate that no longer answers on
    its preview ends try 3 with candidate_not_answering (FR-21.5, FR-21.1)."""
    s = Setup()
    await _interrupt_mid_switch(s)

    s.cluster.answering = set()
    s.log.clear()
    await s.fail_tries(1)
    await s.routing.promote(s.claim())

    assert not any(str(CANDIDATE) in x for x in s.cluster.applied_targets())
    assert s.cluster.route == Route(to_release(LIVE), s.command.command_number)
    candidate = s.world.release(CANDIDATE)
    assert candidate.status is ReleaseStatus.ROLLED_BACK
    assert candidate.failure_code == CODE_CANDIDATE_NOT_ANSWERING
    assert s.failure() == CODE_CANDIDATE_NOT_ANSWERING
    assert s.world.routing() is RoutingStatus.STABLE


# --- promote: failure and restore -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_probe_that_fails_restores_the_route_to_the_live_release() -> None:
    """FR-19.3, FR-21.1: a probe that fails on try 3 restores the route to the live release."""
    s = Setup()
    s.site_stops_answering()
    await s.fail_tries()
    s.log.clear()
    await s.routing.promote(s.claim())

    assert s.cluster.route is not None
    assert s.cluster.route.target == to_release(LIVE)
    assert s.cluster.route.command_number == s.command.command_number
    candidate = s.world.release(CANDIDATE)
    assert candidate.status is ReleaseStatus.ROLLED_BACK
    assert candidate.failure_code == CODE_SITE_NOT_ANSWERING
    assert candidate.promoted_at is None
    assert s.world.release(LIVE).status is ReleaseStatus.LIVE
    assert s.world.hosting.live_release_id == LIVE
    assert s.world.routing() is RoutingStatus.STABLE
    assert s.status() is CommandStatus.FAILED
    assert s.failure() == CODE_SITE_NOT_ANSWERING
    assert s.cluster.applied_targets() == [
        f"apply_route release {CANDIDATE} #{s.command.command_number}",
        f"apply_route release {LIVE} #{s.command.command_number}",
    ]


@pytest.mark.asyncio
async def test_a_route_never_accepted_is_restored_without_probing() -> None:
    """FR-19.2, FR-21.1: a route never accepted on try 3 is restored without probing."""
    s = Setup()
    _switch_not_accepted(s)
    await s.fail_tries()
    s.cluster.serving.add(CANDIDATE)  # the preview of try 3 passes; its switch loses it again
    s.log.clear()
    await s.routing.promote(s.claim())

    assert s.world.release(CANDIDATE).status is ReleaseStatus.ROLLED_BACK
    assert s.failure() == CODE_ROUTE_NOT_ACCEPTED
    assert s.cluster.site_probes() == []
    assert s.cluster.route is not None and s.cluster.route.target == to_release(LIVE)


@pytest.mark.parametrize("refusal", [ClusterRefused("422"), ApplyConflict("409")])
@pytest.mark.asyncio
async def test_a_refused_switch_is_restored_at_once(refusal: Exception) -> None:
    """FR-21.1: on try 3 a refused switch is restored at once, never applied again."""
    s = Setup()
    s.cluster.fail_apply[to_release(CANDIDATE)] = refusal
    await s.fail_tries()
    s.log.clear()
    await s.routing.promote(s.claim())

    assert s.failure() == CODE_ROUTE_REFUSED
    assert s.world.release(CANDIDATE).status is ReleaseStatus.ROLLED_BACK
    assert len([x for x in s.cluster.applied_targets() if str(CANDIDATE) in x]) == 1
    assert s.cluster.site_probes() == []


@pytest.mark.asyncio
async def test_a_first_publish_that_fails_points_the_route_offline_and_fails_the_release() -> None:
    """FR-22.1: Decision 0001 section 2: without a live release, promoting -> failed (rolled_back is
    illegal), and ruling K9: the route goes to the offline page, it is never deleted (try 3)."""
    s = Setup(live=False)
    s.site_stops_answering()
    await s.fail_tries()
    await s.routing.promote(s.claim())

    assert s.cluster.route is not None
    assert s.cluster.route.target == OFFLINE
    assert s.cluster.route.command_number == s.command.command_number
    candidate = s.world.release(CANDIDATE)
    assert candidate.status is ReleaseStatus.FAILED
    assert candidate.failure_code == CODE_SITE_NOT_ANSWERING
    assert s.world.hosting.live_release_id is None
    assert s.world.routing() is RoutingStatus.STABLE
    assert s.status() is CommandStatus.FAILED


@pytest.mark.asyncio
async def test_transient_errors_during_the_restore_are_retried_within_the_deadline() -> None:
    """FR-21.1: the restore of try 3 retries transient cluster errors within its deadline."""
    s = Setup()
    s.site_stops_answering()
    await s.fail_tries()
    s.log.clear()
    s.cluster.fail_apply_once[to_release(LIVE)] = [
        unavailable(),
        TenantNotReady("rolebinding"),
        unavailable(),
    ]
    statuses = 0

    def fail_first_status_of_restore(target: object) -> None:
        nonlocal statuses
        if target == to_release(LIVE) and statuses == 0 and not s.cluster.fail_apply_once[target]:
            statuses += 1
            s.cluster.fail_status_once.append(unavailable())

    s.cluster.on_apply = fail_first_status_of_restore
    await s.routing.promote(s.claim())

    assert s.world.release(CANDIDATE).status is ReleaseStatus.ROLLED_BACK
    assert s.world.routing() is RoutingStatus.STABLE
    assert len([x for x in s.cluster.applied_targets() if str(LIVE) in x]) == 4


@pytest.mark.asyncio
async def test_a_restore_that_fails_leaves_the_route_uncertain_until_an_operator_rollback() -> None:
    """FR-23.1, FR-23.3: a restore that fails leaves the route uncertain until an operator
    rollback."""
    s = Setup()
    s.site_stops_answering()
    s.cluster.fail_apply[to_release(LIVE)] = unavailable()
    await s.routing.promote(s.claim())

    # The restore was not confirmed: uncertain, the command failed, the release still promoting.
    assert s.world.routing() is RoutingStatus.UNCERTAIN
    assert s.status() is CommandStatus.FAILED
    assert s.failure() == CODE_ROUTE_RESTORE_UNCONFIRMED
    assert s.world.release(CANDIDATE).status is ReleaseStatus.PROMOTING
    assert s.world.hosting.live_release_id == LIVE

    # New work is refused: a deploy by its precondition, a suspend too.
    s.world.releases[OTHER_BUILT] = make_release(OTHER_BUILT, ReleaseStatus.BUILT, 3)
    with pytest.raises(RouteNotStable):
        s.world.accept(CommandKind.DEPLOY, OTHER_BUILT)
    with pytest.raises(RouteNotStable):
        s.world.accept(CommandKind.SUSPEND_HOSTING, key="payment-event-1")
    with pytest.raises(RouteNotStable):
        check_command_preconditions(
            CommandKind.DEPLOY,
            s.world.hosting,
            s.world.release(OTHER_BUILT),
            s.world.release(LIVE),
        )

    # The operator's rollback restores the live release and clears it.
    s.cluster.fail_apply.clear()
    rollback = s.world.accept(CommandKind.ROLLBACK, CANDIDATE)
    await s.routing.rollback(s.world.claim(rollback))

    assert s.world.routing() is RoutingStatus.STABLE
    assert s.world.command(rollback.id).status is CommandStatus.SUCCEEDED
    candidate = s.world.release(CANDIDATE)
    assert candidate.status is ReleaseStatus.ROLLED_BACK
    assert candidate.failure_code == CODE_OPERATOR_ROLLBACK
    assert s.cluster.route is not None
    assert s.cluster.route.target == to_release(LIVE)
    assert s.cluster.route.command_number == rollback.command_number
    # And new work is accepted again.
    deploy = s.world.accept(CommandKind.DEPLOY, OTHER_BUILT)
    assert deploy.status is CommandStatus.ACCEPTED


@pytest.mark.asyncio
async def test_an_operator_rollback_after_a_first_publish_points_the_route_offline() -> None:
    s = Setup(live=False)
    s.site_stops_answering()
    s.cluster.fail_apply[OFFLINE] = unavailable()
    await s.routing.promote(s.claim())
    assert s.world.routing() is RoutingStatus.UNCERTAIN

    s.cluster.fail_apply.clear()
    rollback = s.world.accept(CommandKind.ROLLBACK, CANDIDATE)
    await s.routing.rollback(s.world.claim(rollback))

    assert s.cluster.route is not None and s.cluster.route.target == OFFLINE
    assert s.world.release(CANDIDATE).status is ReleaseStatus.FAILED
    assert s.world.command(rollback.id).status is CommandStatus.SUCCEEDED
    assert s.world.routing() is RoutingStatus.STABLE


@pytest.mark.asyncio
async def test_an_operator_rollback_that_cannot_confirm_leaves_the_route_uncertain() -> None:
    s = Setup()
    s.site_stops_answering()
    s.cluster.fail_apply[to_release(LIVE)] = unavailable()
    await s.routing.promote(s.claim())

    rollback = s.world.accept(CommandKind.ROLLBACK, CANDIDATE)
    await s.routing.rollback(s.world.claim(rollback))

    assert s.world.routing() is RoutingStatus.UNCERTAIN
    assert s.world.command(rollback.id).status is CommandStatus.FAILED
    assert s.world.command(rollback.id).failure_code == CODE_ROUTE_RESTORE_UNCONFIRMED
    assert s.world.release(CANDIDATE).status is ReleaseStatus.PROMOTING


# --- lost claims, resumed claims, fences --------------------------------------------------------


@pytest.mark.asyncio
async def test_a_lost_claim_mid_switch_writes_nothing_and_the_next_claim_inspects_the_route() -> (
    None
):
    s = Setup()
    first = s.claim()
    stolen: list[str] = []

    def steal_once(hostname: str) -> None:
        if hostname == HOSTNAME and not stolen:
            stolen.append(hostname)
            s.world.steal_claim(s.command.id)  # another worker's claim 2, which then lapsed

    s.cluster.on_probe = steal_once

    with pytest.raises(ClaimLost):
        await s.routing.promote(first)

    # Only the intent was written; SQL still says changing and the release promoting.
    assert s.world.writes == [
        f"release {CANDIDATE} promoting",
        f"hosting routing=changing hosting=online live={LIVE}",
    ]
    assert s.world.routing() is RoutingStatus.CHANGING

    # The next claim (the one that took over) reads the actual route before acting.
    s.cluster.on_probe = None
    s.log.clear()
    third = s.world.claim(s.command)
    assert third.execution_attempt == GO_LIVE_TRIES  # the last try, which passes
    await s.routing.promote(third)

    cluster_calls = [x for x in s.log if x.startswith(("apply_route", "route_status", "probe"))]
    assert cluster_calls[0] == "route_status"
    assert "entitlement" not in " ".join(s.log)
    assert s.world.release(CANDIDATE).status is ReleaseStatus.LIVE
    assert s.status() is CommandStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_a_claim_resumed_during_a_restore_finishes_the_restore() -> None:
    """FR-21.5: try 3's restore was cut off by a lost claim; the next claim is past the last try,
    so it runs no try, finishes the restore and ends promotion_interrupted."""
    s = Setup()
    s.site_stops_answering()
    await s.fail_tries()
    first = s.claim()

    def steal_when_restoring(target: object) -> None:
        if target == to_release(LIVE):
            s.world.steal_claim(s.command.id)

    s.cluster.on_apply = steal_when_restoring
    with pytest.raises(ClaimLost):
        await s.routing.promote(first)
    assert s.world.routing() is RoutingStatus.CHANGING
    assert s.cluster.route is not None and s.cluster.route.target == to_release(LIVE)

    s.cluster.on_apply = None
    s.cluster.answering = {CANDIDATE}  # would answer now, but the restore was already under way
    s.log.clear()
    last = s.world.claim(s.command)
    assert last.execution_attempt > GO_LIVE_TRIES
    await s.routing.promote(last)

    assert not any(str(CANDIDATE) in x for x in s.cluster.applied_targets())
    candidate = s.world.release(CANDIDATE)
    assert candidate.status is ReleaseStatus.ROLLED_BACK
    assert candidate.failure_code == CODE_PROMOTION_INTERRUPTED
    assert s.status() is CommandStatus.FAILED
    assert s.world.routing() is RoutingStatus.STABLE


@pytest.mark.asyncio
async def test_a_claim_resumed_before_the_switch_landed_switches_again() -> None:
    """FR-21.2, FR-21.3: try 1's switch never landed, so the route is still on the live release
    at an older command's number: nothing is written back, and try 2 switches again."""
    s = Setup()
    s.cluster.fail_apply[to_release(CANDIDATE)] = unavailable()
    with pytest.raises(RetryLater) as raised:
        await s.routing.promote(s.claim())
    assert raised.value.code == CODE_ROUTE_NOT_ACCEPTED
    assert s.cluster.route == Route(to_release(LIVE), 0)

    s.cluster.fail_apply.clear()
    s.log.clear()
    await s.routing.promote(s.claim())

    number = s.command.command_number
    assert s.cluster.applied_targets() == [f"apply_route release {CANDIDATE} #{number}"]
    assert s.world.release(CANDIDATE).status is ReleaseStatus.LIVE


@pytest.mark.asyncio
async def test_a_cancelled_promotion_writes_nothing_more() -> None:
    s = Setup()
    s.site_stops_answering()
    probing = asyncio.Event()
    s.cluster.on_probe = lambda h: probing.set() if h == HOSTNAME else None
    task = asyncio.create_task(s.routing.promote(s.claim()))
    async with asyncio.timeout(5):
        await probing.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert s.world.routing() is RoutingStatus.CHANGING
    assert s.world.release(CANDIDATE).status is ReleaseStatus.PROMOTING
    assert s.status() is CommandStatus.RUNNING


@pytest.mark.asyncio
async def test_a_stale_fence_touches_neither_sql_nor_the_cluster() -> None:
    s = Setup()
    claim = s.claim()
    s.world.hosting = replace(s.world.hosting, last_command_number=claim.ref.command_number + 1)
    with pytest.raises(StaleFence):
        await s.routing.promote(claim)
    assert s.world.writes == []
    assert s.cluster.applied_targets() == []


# --- the go-live tries (FR-19, FR-21, FR-22; TR-57; decision 0027) ------------------------------

THIRTY = timedelta(seconds=30)
SIXTY = timedelta(seconds=60)
INTENT = [f"release {CANDIDATE} promoting", f"hosting routing=changing hosting=online live={LIVE}"]


def _preview_refused(s: Setup) -> None:
    s.cluster.fail_preview = ClusterRefused("422")


def _preview_not_confirmed(s: Setup) -> None:
    s.cluster.serving = {LIVE}  # the candidate's Service never resolves on the preview route


def _candidate_silent(s: Setup) -> None:
    s.cluster.answering = set()


def _candidate_503(s: Setup) -> None:
    s.cluster.outage = {CANDIDATE}


def _switch_refused(s: Setup) -> None:
    s.cluster.fail_apply[to_release(CANDIDATE)] = ClusterRefused("422")


def _switch_not_accepted(s: Setup) -> None:
    def lose_the_service(target: RouteTarget) -> None:
        if target == to_release(CANDIDATE):  # gone after the preview: ResolvedRefs never true
            s.cluster.serving.discard(CANDIDATE)

    s.cluster.on_apply = lose_the_service


def _site_silent(s: Setup) -> None:
    s.site_stops_answering()


async def _interrupt_mid_switch(s: Setup) -> None:
    """Run the next claim until the site is probed after the switch, then cancel it (a worker
    killed mid-try): the route is left on the candidate at this command's number."""
    probing = asyncio.Event()
    s.cluster.on_probe = lambda h: probing.set() if h == HOSTNAME else None
    task = asyncio.create_task(s.routing.promote(s.claim()))
    async with asyncio.timeout(5):
        await probing.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    s.cluster.on_probe = None
    assert s.cluster.route == Route(to_release(CANDIDATE), s.command.command_number)
    assert s.world.routing() is RoutingStatus.CHANGING


@pytest.mark.asyncio
async def test_fr21_6_on_a_fresh_start_the_intent_is_stored_before_apply_preview() -> None:
    """FR-21.6, FR-19.1: entitlement, then the intent, then the preview, then the switch; every
    probe expects the candidate."""
    s = Setup()
    await s.routing.promote(s.claim())

    number = s.command.command_number
    assert s.cluster.preview == Route(to_release(CANDIDATE), number)
    assert s.cluster.preview_host == PREVIEW_HOSTNAME
    order = [
        s.log.index(f"entitlement {CommandKind.PROMOTE}"),
        s.log.index("store apply"),
        s.log.index(f"apply_preview release {CANDIDATE} #{number}"),
        s.log.index("preview_status"),
        s.log.index(f"probe {PREVIEW_HOSTNAME} expect release {CANDIDATE}"),
        s.log.index(f"apply_route release {CANDIDATE} #{number}"),
        s.log.index(f"probe {HOSTNAME} expect release {CANDIDATE}"),
    ]
    assert order == sorted(order)
    probes = [x for x in s.log if x.startswith("probe ")]
    assert probes and all(x.endswith(f"expect release {CANDIDATE}") for x in probes)
    assert s.status() is CommandStatus.SUCCEEDED


@pytest.mark.parametrize(
    ("fault", "code"),
    [
        (_preview_refused, CODE_ROUTE_REFUSED),
        (_preview_not_confirmed, CODE_ROUTE_NOT_ACCEPTED),
        (_candidate_silent, CODE_CANDIDATE_NOT_ANSWERING),
        (_candidate_503, CODE_DEPENDENCY_UNAVAILABLE),
        (_switch_refused, CODE_ROUTE_REFUSED),
        (_switch_not_accepted, CODE_ROUTE_NOT_ACCEPTED),
        (_site_silent, CODE_SITE_NOT_ANSWERING),
    ],
    ids=[
        "preview-refused",
        "preview-not-confirmed",
        "candidate-silent",
        "candidate-503",
        "switch-refused",
        "switch-not-accepted",
        "site-silent",
    ],
)
@pytest.mark.asyncio
async def test_fr19_each_failed_step_gives_its_try_code(
    fault: Callable[[Setup], None], code: str
) -> None:
    """FR-19.2, FR-19.3, FR-21.2, FR-21.6: each step of a try that fails gives its try code
    (design G5); try 1 ends with the route on the live release and a wait of 30 s."""
    s = Setup()
    fault(s)
    with pytest.raises(RetryLater) as raised:
        await s.routing.promote(s.claim())

    assert (raised.value.code, raised.value.after) == (code, THIRTY)
    assert s.cluster.route is not None and s.cluster.route.target == to_release(LIVE)
    assert s.world.release(CANDIDATE).status is ReleaseStatus.PROMOTING


@pytest.mark.asyncio
async def test_fr19_2_a_switch_not_accepted_on_try_1_points_back_to_l_and_waits_30_s() -> None:
    """FR-19.2, FR-21.2: the switched route is never accepted; the route goes back to the live
    release at this command's number, nothing is probed, and the try waits 30 s."""
    s = Setup()
    _switch_not_accepted(s)
    with pytest.raises(RetryLater) as raised:
        await s.routing.promote(s.claim())

    assert (raised.value.code, raised.value.after) == (CODE_ROUTE_NOT_ACCEPTED, THIRTY)
    assert s.cluster.route == Route(to_release(LIVE), s.command.command_number)
    assert s.cluster.site_probes() == []
    assert s.world.release(CANDIDATE).status is ReleaseStatus.PROMOTING


@pytest.mark.asyncio
async def test_fr19_3_a_site_not_answering_on_try_1_points_back_to_l_and_waits_30_s() -> None:
    """FR-19.3, FR-21.2: the site does not answer with the candidate after the switch; the route
    goes back to the live release and the try waits 30 s."""
    s = Setup()
    s.site_stops_answering()
    with pytest.raises(RetryLater) as raised:
        await s.routing.promote(s.claim())

    assert (raised.value.code, raised.value.after) == (CODE_SITE_NOT_ANSWERING, THIRTY)
    number = s.command.command_number
    assert s.cluster.route == Route(to_release(LIVE), number)
    assert s.cluster.applied_targets() == [
        f"apply_route release {CANDIDATE} #{number}",
        f"apply_route release {LIVE} #{number}",
    ]


@pytest.mark.asyncio
async def test_fr21_2_a_failed_try_1_writes_nothing_after_the_intent_and_waits_30_s() -> None:
    """FR-21.2: between tries the release stays promoting, routing changing, the command
    running and the live release live; the only SQL write of the claim is the intent."""
    s = Setup()
    s.site_stops_answering()
    with pytest.raises(RetryLater) as raised:
        await s.routing.promote(s.claim())

    assert (raised.value.code, raised.value.after) == (CODE_SITE_NOT_ANSWERING, THIRTY)
    assert s.world.writes == INTENT
    assert s.world.release(CANDIDATE).status is ReleaseStatus.PROMOTING
    assert s.world.release(CANDIDATE).failure_code is None
    assert s.world.routing() is RoutingStatus.CHANGING
    assert s.status() is CommandStatus.RUNNING
    assert s.world.hosting.live_release_id == LIVE
    assert s.world.release(LIVE).status is ReleaseStatus.LIVE


@pytest.mark.asyncio
async def test_fr21_2_a_restore_after_try_1_not_confirmed_is_route_uncertain_without_retry_later() -> (  # noqa: E501 - the plan's test name
    None
):
    """FR-21.2, FR-23.1: the route cannot be confirmed back on the live release after try 1, so
    the route is uncertain and the command ends; no next try."""
    s = Setup()
    s.site_stops_answering()
    s.cluster.fail_apply[to_release(LIVE)] = unavailable()
    await s.routing.promote(s.claim())

    assert s.world.routing() is RoutingStatus.UNCERTAIN
    assert s.status() is CommandStatus.FAILED
    assert s.failure() == CODE_ROUTE_RESTORE_UNCONFIRMED
    assert s.world.release(CANDIDATE).status is ReleaseStatus.PROMOTING


@pytest.mark.asyncio
async def test_fr21_3_claim_2_passing_reads_the_route_once_and_goes_live() -> None:
    """FR-21.3: the route is already back on the live release, so claim 2 reads it once, writes
    nothing to it, asks no entitlement, records no second intent, and goes live."""
    s = Setup()
    s.site_stops_answering()
    await run_failed_tries(s.routing, s.world, s.command, 1)

    s.cluster.answering = {CANDIDATE}
    s.log.clear()
    await s.routing.promote(s.claim())

    cluster = [x for x in s.log if x.startswith(("apply_", "route_status", "preview_status"))]
    assert cluster[0] == "route_status"
    assert cluster[1].startswith("apply_preview")
    assert not any("entitlement" in x for x in s.log)
    assert s.world.writes.count(f"release {CANDIDATE} promoting") == 1
    assert s.world.release(CANDIDATE).status is ReleaseStatus.LIVE
    assert s.world.release(LIVE).status is ReleaseStatus.SUPERSEDED
    assert s.status() is CommandStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_fr21_4_a_failed_try_2_waits_60_s() -> None:
    """FR-21.4: try 2 fails too; the route is back on the live release and the next try waits
    60 s."""
    s = Setup()
    s.site_stops_answering()
    raised = await run_failed_tries(s.routing, s.world, s.command, 2)

    assert [(r.code, r.after) for r in raised] == [
        (CODE_SITE_NOT_ANSWERING, THIRTY),
        (CODE_SITE_NOT_ANSWERING, SIXTY),
    ]
    assert s.cluster.route == Route(to_release(LIVE), s.command.command_number)
    assert s.world.release(CANDIDATE).status is ReleaseStatus.PROMOTING
    assert s.status() is CommandStatus.RUNNING


@pytest.mark.asyncio
async def test_fr21_1_try_3_failing_restores_l_and_rolls_back_with_its_code() -> None:
    """FR-21.1: try 3 fails; the route is on the live release at this command's number, the
    release rolled_back with try 3's code, the command failed, routing stable, never live."""
    s = Setup()
    s.site_stops_answering()
    await run_failed_tries(s.routing, s.world, s.command, 2)
    s.cluster.fail_apply[to_release(CANDIDATE)] = ClusterRefused("422")  # try 3's own failure

    await s.routing.promote(s.claim())

    assert s.cluster.route == Route(to_release(LIVE), s.command.command_number)
    candidate = s.world.release(CANDIDATE)
    assert (candidate.status, candidate.failure_code) == (
        ReleaseStatus.ROLLED_BACK,
        CODE_ROUTE_REFUSED,
    )
    assert (s.status(), s.failure()) == (CommandStatus.FAILED, CODE_ROUTE_REFUSED)
    assert s.world.routing() is RoutingStatus.STABLE
    assert s.world.release(LIVE).status is ReleaseStatus.LIVE
    assert f"release {CANDIDATE} live" not in s.world.writes


@pytest.mark.asyncio
async def test_fr21_5_claim_2_after_a_lapse_points_the_route_back_before_its_preview() -> None:
    """FR-21.5: try 1 was killed after its switch landed; claim 2 is try 2 and first points the
    route back at the live release, so it is never left on the unproven candidate."""
    s = Setup()
    await _interrupt_mid_switch(s)

    s.log.clear()
    claim = s.claim()
    assert claim.execution_attempt == 2
    await s.routing.promote(claim)

    number = s.command.command_number
    assert s.log.index(f"apply_route release {LIVE} #{number}") < s.log.index(
        f"apply_preview release {CANDIDATE} #{number}"
    )
    assert s.status() is CommandStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_fr21_5_an_unreadable_route_is_pointed_back_before_the_try() -> None:
    """FR-21.5: claim 2 cannot read the route; it points it at the live release all the same
    before its preview."""
    s = Setup()
    s.site_stops_answering()
    await run_failed_tries(s.routing, s.world, s.command, 1)

    s.cluster.answering = {CANDIDATE}
    s.cluster.fail_status_once = [unavailable()]
    s.log.clear()
    await s.routing.promote(s.claim())

    number = s.command.command_number
    assert s.log[s.log.index("route_status") + 1] == f"apply_route release {LIVE} #{number}"
    assert s.log.index(f"apply_route release {LIVE} #{number}") < s.log.index(
        f"apply_preview release {CANDIDATE} #{number}"
    )
    assert s.status() is CommandStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_fr21_5_a_resumed_claim_whose_restore_first_is_not_confirmed_is_route_uncertain() -> (
    None
):
    """FR-21.5, FR-23.1: the route cannot be put back before try 2, so it is uncertain and the
    command ends without a preview."""
    s = Setup()
    await _interrupt_mid_switch(s)
    s.cluster.fail_apply[to_release(LIVE)] = unavailable()

    s.log.clear()
    await s.routing.promote(s.claim())

    assert s.world.routing() is RoutingStatus.UNCERTAIN
    assert (s.status(), s.failure()) == (CommandStatus.FAILED, CODE_ROUTE_RESTORE_UNCONFIRMED)
    assert s.cluster.preview_probes() == []
    assert not any(x.startswith("apply_preview") for x in s.log)


@pytest.mark.asyncio
async def test_fr21_5_claim_4_on_a_changing_route_ends_promotion_interrupted() -> None:
    """FR-21.5: try 3 was killed after its switch; claim 4 runs no try, puts the route back, and
    ends the promotion promotion_interrupted."""
    s = Setup()
    s.site_stops_answering()
    await run_failed_tries(s.routing, s.world, s.command, 2)
    s.cluster.answering = {CANDIDATE}
    await _interrupt_mid_switch(s)

    s.log.clear()
    claim = s.claim()
    assert claim.execution_attempt == 4
    await s.routing.promote(claim)

    assert not any(x.startswith("apply_preview") for x in s.log)
    assert not any(str(CANDIDATE) in x for x in s.cluster.applied_targets())
    assert s.cluster.route == Route(to_release(LIVE), s.command.command_number)
    candidate = s.world.release(CANDIDATE)
    assert (candidate.status, candidate.failure_code) == (
        ReleaseStatus.ROLLED_BACK,
        CODE_PROMOTION_INTERRUPTED,
    )
    assert (s.status(), s.failure()) == (CommandStatus.FAILED, CODE_PROMOTION_INTERRUPTED)
    assert s.world.routing() is RoutingStatus.STABLE


@pytest.mark.asyncio
async def test_fr21_5_claim_4_on_a_stable_route_is_attempts_exhausted() -> None:
    """FR-21.5: three claims ended before any intent was written; claim 4 runs no try and the
    command fails attempts_exhausted with the release still candidate_ready."""
    s = Setup()
    for _ in range(3):
        s.claim()  # each claim lapsed before it wrote anything
    s.log.clear()

    with pytest.raises(PermanentFailure) as raised:
        await s.routing.promote(s.claim())

    assert raised.value.code == CODE_ATTEMPTS_EXHAUSTED
    assert s.world.writes == []
    assert s.world.release(CANDIDATE).status is ReleaseStatus.CANDIDATE_READY
    assert [x for x in s.log if not x.startswith("store")] == []


@pytest.mark.asyncio
async def test_fr21_6_a_fresh_start_preview_failure_never_moves_the_route() -> None:
    """FR-21.6: the candidate never answers on its preview; the site's route is never written
    (it already points at the live release) and try 1 waits 30 s with the release promoting."""
    s = Setup()
    s.cluster.answering = set()
    with pytest.raises(RetryLater) as raised:
        await s.routing.promote(s.claim())

    assert (raised.value.code, raised.value.after) == (CODE_CANDIDATE_NOT_ANSWERING, THIRTY)
    assert s.cluster.applied_targets() == []
    assert s.cluster.route == Route(to_release(LIVE), 0)
    assert s.cluster.site_probes() == []
    assert s.world.writes == INTENT


@pytest.mark.asyncio
async def test_fr21_6_try_3_preview_failure_ends_as_fr21_1() -> None:
    """FR-21.6, FR-21.1: the preview fails on all three tries; try 3 ends through the restore,
    which stamps the route with this command's number, and the release is rolled_back."""
    s = Setup()
    s.cluster.answering = set()
    await run_failed_tries(s.routing, s.world, s.command, 2)
    assert s.cluster.applied_targets() == []

    await s.routing.promote(s.claim())

    number = s.command.command_number
    assert s.cluster.applied_targets() == [f"apply_route release {LIVE} #{number}"]
    assert s.cluster.route == Route(to_release(LIVE), number)
    candidate = s.world.release(CANDIDATE)
    assert (candidate.status, candidate.failure_code) == (
        ReleaseStatus.ROLLED_BACK,
        CODE_CANDIDATE_NOT_ANSWERING,
    )
    assert (s.status(), s.failure()) == (CommandStatus.FAILED, CODE_CANDIDATE_NOT_ANSWERING)
    assert s.world.routing() is RoutingStatus.STABLE


@pytest.mark.asyncio
async def test_fr22_1_no_live_release_try_3_failing_goes_offline_and_fails_with_its_code() -> None:
    """FR-22.1: a first deployment whose three tries fail: the route shows the offline page at
    this command's number and the release fails with try 3's code."""
    s = Setup(live=False)
    s.site_stops_answering()
    await run_failed_tries(s.routing, s.world, s.command, 2)

    await s.routing.promote(s.claim())

    assert s.cluster.route == Route(OFFLINE, s.command.command_number)
    candidate = s.world.release(CANDIDATE)
    assert (candidate.status, candidate.failure_code) == (
        ReleaseStatus.FAILED,
        CODE_SITE_NOT_ANSWERING,
    )
    assert (s.status(), s.failure()) == (CommandStatus.FAILED, CODE_SITE_NOT_ANSWERING)
    assert s.world.hosting.live_release_id is None
    assert s.world.routing() is RoutingStatus.STABLE


@pytest.mark.asyncio
async def test_fr22_2_no_live_release_try_1_failing_points_an_absent_route_offline() -> None:
    """FR-22.2: a first deployment has no site route yet; after a failed try 1 it shows the
    offline page and the release stays promoting."""
    s = Setup(live=False)
    before = s.cluster.route
    assert before is None  # no site route yet
    s.cluster.answering = set()
    with pytest.raises(RetryLater) as raised:
        await s.routing.promote(s.claim())

    assert (raised.value.code, raised.value.after) == (CODE_CANDIDATE_NOT_ANSWERING, THIRTY)
    assert s.cluster.route == Route(OFFLINE, s.command.command_number)
    assert s.world.release(CANDIDATE).status is ReleaseStatus.PROMOTING
    assert s.world.routing() is RoutingStatus.CHANGING
    assert s.status() is CommandStatus.RUNNING


# --- entitlement at the start ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_entitlement_lost_at_the_start_fails_the_command_without_touching_anything() -> None:
    s = Setup()
    s.entitlement.refuse = True
    with pytest.raises(PermanentFailure) as raised:
        await s.routing.promote(s.claim())
    assert raised.value.code == CODE_ENTITLEMENT_LOST
    assert s.world.writes == []
    assert s.cluster.applied_targets() == []
    assert s.world.release(CANDIDATE).status is ReleaseStatus.CANDIDATE_READY


# --- Payment cannot be asked: a failed try (FR-21; TR-57; design G10) ----------------------------

PAYMENT_DOWN = "payment at 10.0.0.7 refused token abc"
TRY_LOGGER = "provisioning.core.application.routing.background.routing"
TRY_FIELDS = (
    "command_id",
    "project_id",
    "release_id",
    "try",
    "of",
    "failure_code",
    "next_try_after_seconds",
)


def _payment_down(s: Setup) -> None:
    s.entitlement.fault = ConnectionError(PAYMENT_DOWN)


@pytest.mark.asyncio
async def test_fr21_2_payment_unreachable_on_try_1_waits_30_s_and_touches_nothing() -> None:
    """FR-21.2 (G10): Payment cannot answer the entitlement recheck of try 1: a failed try with
    ``transient_failure`` and a wait of 30 s. Nothing was touched: no intent, no cluster call,
    routing stable, the release candidate_ready. The error's text never travels."""
    s = Setup()
    _payment_down(s)
    with pytest.raises(RetryLater) as raised:
        await s.routing.promote(s.claim())

    assert (raised.value.code, raised.value.after) == (FAILURE_CODE_TRANSIENT, THIRTY)
    assert "abc" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert s.world.writes == []
    assert [x for x in s.log if x.startswith(("apply_", "route_status", "probe"))] == []
    assert s.world.routing() is RoutingStatus.STABLE
    assert s.world.release(CANDIDATE).status is ReleaseStatus.CANDIDATE_READY


@pytest.mark.asyncio
async def test_fr21_4_payment_unreachable_on_try_2_waits_60_s() -> None:
    """FR-21.4 (G10): Payment still cannot answer on try 2: the next try waits 60 s."""
    s = Setup()
    _payment_down(s)
    raised = await s.fail_tries()

    assert [(r.code, r.after) for r in raised] == [
        (FAILURE_CODE_TRANSIENT, THIRTY),
        (FAILURE_CODE_TRANSIENT, SIXTY),
    ]
    assert s.world.writes == []


@pytest.mark.asyncio
async def test_fr21_1_payment_unreachable_on_try_3_is_attempts_exhausted() -> None:
    """FR-21.1 (G10, design question Q-3): Payment cannot answer on any of the 3 tries: the
    command fails ``attempts_exhausted`` and the release stays candidate_ready, as nothing was
    touched."""
    s = Setup()
    _payment_down(s)
    await s.fail_tries()

    with pytest.raises(PermanentFailure) as raised:
        await s.routing.promote(s.claim())

    assert raised.value.code == CODE_ATTEMPTS_EXHAUSTED
    assert "abc" not in str(raised.value)
    assert s.world.writes == []
    assert s.world.release(CANDIDATE).status is ReleaseStatus.CANDIDATE_READY
    assert s.cluster.applied_targets() == []


def _try_lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, str]]:
    """The fields of each try log line: ``go-live try failed: k=v k=v ...``."""
    out: list[dict[str, str]] = []
    for record in caplog.records:
        if record.name != TRY_LOGGER:
            continue
        prefix, _, rest = record.getMessage().partition(": ")
        assert prefix == "go-live try failed"
        assert record.levelno == logging.INFO
        assert record.exc_info is None
        out.append(dict(pair.split("=", 1) for pair in rest.split(" ")))
    return out


@pytest.mark.asyncio
async def test_tr57_each_failed_try_logs_one_info_line(caplog: pytest.LogCaptureFixture) -> None:
    """TR-57 (G15): one info line per failed try, route tries and Payment tries alike, naming the
    try, the code and the wait before the next try (none after try 3)."""
    caplog.set_level(logging.INFO, logger=TRY_LOGGER)
    s = Setup()
    s.site_stops_answering()
    await s.fail_tries()
    await s.routing.promote(s.claim())

    ids = {
        "command_id": str(s.command.id),
        "project_id": str(PROJECT),
        "release_id": str(CANDIDATE),
        "of": "3",
        "failure_code": CODE_SITE_NOT_ANSWERING,
    }
    assert _try_lines(caplog) == [
        {**ids, "try": "1", "next_try_after_seconds": "30"},
        {**ids, "try": "2", "next_try_after_seconds": "60"},
        {**ids, "try": "3", "next_try_after_seconds": "none"},
    ]

    caplog.clear()
    payment = Setup()
    _payment_down(payment)
    await payment.fail_tries(1)
    (line,) = _try_lines(caplog)
    assert (line["try"], line["failure_code"], line["next_try_after_seconds"]) == (
        "1",
        FAILURE_CODE_TRANSIENT,
        "30",
    )


@pytest.mark.asyncio
async def test_tr57_the_try_log_line_holds_only_its_named_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """TR-57 (G15): the line holds its seven named fields and nothing else, so no token,
    credential, error text or site content: the Payment error's text never appears."""
    caplog.set_level(logging.DEBUG, logger=TRY_LOGGER)
    s = Setup()
    _payment_down(s)
    await s.fail_tries()
    with pytest.raises(PermanentFailure):
        await s.routing.promote(s.claim())

    lines = _try_lines(caplog)
    assert len(lines) == 3
    assert all(tuple(line) == TRY_FIELDS for line in lines)
    text = caplog.text
    assert "abc" not in text and "10.0.0.7" not in text and "token" not in text


# --- the route pointer ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_pointer_reports_unconfirmed_when_the_deadline_ends_a_hanging_call() -> None:
    log: list[str] = []
    cluster = FakeCluster(log, serving={CANDIDATE})
    cluster.hang_apply = True
    pointer = RoutePointer(cluster, settings())
    result = await pointer.point(
        PROJECT, to_release(CANDIDATE), 7, timedelta(milliseconds=30), subdomain=SUBDOMAIN
    )
    assert result is PointResult.UNCONFIRMED


@pytest.mark.asyncio
async def test_the_pointer_does_not_apply_again_once_applied() -> None:
    log: list[str] = []
    cluster = FakeCluster(log, serving=set())
    pointer = RoutePointer(cluster, settings())
    result = await pointer.point(
        PROJECT, to_release(CANDIDATE), 7, timedelta(milliseconds=30), subdomain=SUBDOMAIN
    )
    assert result is PointResult.UNCONFIRMED
    assert len(cluster.applied_targets()) == 1
    assert log.count("route_status") >= 2


@pytest.mark.asyncio
async def test_the_pointer_does_not_take_an_older_commands_route_for_its_own() -> None:
    """The route already points at the target, but at an older command's number, and this
    command's apply is not visible yet: that is not a confirmation."""
    log: list[str] = []
    cluster = FakeCluster(log, serving={LIVE}, route=Route(to_release(LIVE), 3), apply_lands=False)
    pointer = RoutePointer(cluster, settings())
    result = await pointer.point(
        PROJECT, to_release(LIVE), 7, timedelta(milliseconds=30), subdomain=SUBDOMAIN
    )
    assert result is PointResult.UNCONFIRMED


# --- decision 0014 section 13: an operator rollback clears any uncertain route -------------


class HostingSetup:
    """A live project (or one without a live release) whose suspend or resume could not confirm
    its route, so routing is uncertain with no promoting release."""

    def __init__(self, *, live: bool = True) -> None:
        self.log: list[str] = []
        self.world = World(self.log, live=live, candidate=False)
        self.cluster = FakeCluster(self.log, serving={LIVE})
        if live:
            self.cluster.route = Route(to_release(LIVE), 0)
        self.entitlement = FakeHostingEntitlement(self.log)
        self.hosting = Hosting(
            store=self.world,
            cluster=self.cluster,
            entitlement=self.entitlement,
            clock=self.world.clock,
            settings=settings(),
        )
        self.routing = Routing(
            store=self.world,
            cluster=self.cluster,
            entitlement=FakeEntitlement(self.log),
            clock=self.world.clock,
            settings=settings(),
        )

    async def hosting_command(self, kind: CommandKind, key: str) -> CommandStatus:
        command = self.world.accept(kind, key=key)
        claim = self.world.claim(command)
        if kind is CommandKind.SUSPEND_HOSTING:
            await self.hosting.suspend(claim)
        else:
            await self.hosting.resume(claim)
        return self.world.command(command.id).status

    async def rollback(self, release_id: UUID) -> Command:
        command = self.world.accept(CommandKind.ROLLBACK, release_id)
        await self.routing.rollback(self.world.claim(command))
        return self.world.command(command.id)


@pytest.mark.asyncio
async def test_a_rollback_after_a_failed_suspend_restores_the_recorded_state() -> None:
    """The failed suspend left hosting online, so the route goes back to the live release."""
    s = HostingSetup()
    s.cluster.fail_apply[OFFLINE] = unavailable()
    assert await s.hosting_command(CommandKind.SUSPEND_HOSTING, "evt-ended") is CommandStatus.FAILED
    assert s.world.routing() is RoutingStatus.UNCERTAIN
    assert s.world.hosting_status() is HostingStatus.ONLINE

    s.cluster.fail_apply.clear()
    rollback = await s.rollback(LIVE)

    assert rollback.status is CommandStatus.SUCCEEDED
    assert s.cluster.route == Route(to_release(LIVE), rollback.command_number)
    assert s.world.routing() is RoutingStatus.STABLE
    assert s.world.hosting_status() is HostingStatus.ONLINE
    assert s.world.release(LIVE).status is ReleaseStatus.LIVE
    # Payment's events are accepted again.
    assert s.world.accept(CommandKind.SUSPEND_HOSTING, key="evt-ended-again").status is (
        CommandStatus.ACCEPTED
    )


@pytest.mark.asyncio
async def test_a_rollback_after_a_failed_resume_shows_the_offline_page() -> None:
    """The failed resume left hosting offline, so the route goes to the offline page."""
    s = HostingSetup()
    assert await s.hosting_command(CommandKind.SUSPEND_HOSTING, "evt-1") is CommandStatus.SUCCEEDED
    s.cluster.fail_apply[to_release(LIVE)] = unavailable()
    assert await s.hosting_command(CommandKind.RESUME_HOSTING, "evt-2") is CommandStatus.FAILED
    assert s.world.routing() is RoutingStatus.UNCERTAIN
    assert s.world.hosting_status() is HostingStatus.OFFLINE

    s.cluster.fail_apply.clear()
    rollback = await s.rollback(LIVE)

    assert rollback.status is CommandStatus.SUCCEEDED
    assert s.cluster.route == Route(OFFLINE, rollback.command_number)
    assert s.world.routing() is RoutingStatus.STABLE
    assert s.world.hosting_status() is HostingStatus.OFFLINE


@pytest.mark.asyncio
async def test_a_rollback_without_a_live_release_names_the_failed_release_and_goes_offline() -> (
    None
):
    # A project without a live release reaches an uncertain route through a failed first
    # promote: the candidate does not answer and the route back to offline cannot be confirmed.
    # (A suspend never routes such a project, decision 0021 D6.)
    s = Setup(live=False)
    s.site_stops_answering()
    s.cluster.fail_apply[OFFLINE] = unavailable()
    await s.routing.promote(s.claim())
    assert s.status() is CommandStatus.FAILED
    assert s.world.routing() is RoutingStatus.UNCERTAIN

    s.cluster.fail_apply.clear()
    command = s.world.accept(CommandKind.ROLLBACK, CANDIDATE)
    await s.routing.rollback(s.world.claim(command))
    rollback = s.world.command(command.id)

    assert rollback.status is CommandStatus.SUCCEEDED
    assert s.cluster.route == Route(OFFLINE, rollback.command_number)
    assert s.world.routing() is RoutingStatus.STABLE
    assert s.world.release(CANDIDATE).status is ReleaseStatus.FAILED
    assert s.world.hosting.live_release_id is None


@pytest.mark.asyncio
async def test_a_rollback_naming_the_live_release_still_rolls_back_the_candidate() -> None:
    s = Setup()
    s.site_stops_answering()
    s.cluster.fail_apply[to_release(LIVE)] = unavailable()
    await s.routing.promote(s.claim())
    assert s.world.routing() is RoutingStatus.UNCERTAIN

    s.cluster.fail_apply.clear()
    rollback = s.world.accept(CommandKind.ROLLBACK, LIVE)
    await s.routing.rollback(s.world.claim(rollback))

    assert s.world.command(rollback.id).status is CommandStatus.SUCCEEDED
    assert s.world.release(CANDIDATE).status is ReleaseStatus.ROLLED_BACK
    assert s.world.release(LIVE).status is ReleaseStatus.LIVE
    assert s.cluster.route == Route(to_release(LIVE), rollback.command_number)
    assert s.world.routing() is RoutingStatus.STABLE


def test_a_rollback_on_a_stable_route_is_still_refused() -> None:
    log: list[str] = []
    world = World(log)
    with pytest.raises(IllegalTransition):
        world.accept(CommandKind.ROLLBACK, LIVE)
    assert world.commands == {}
