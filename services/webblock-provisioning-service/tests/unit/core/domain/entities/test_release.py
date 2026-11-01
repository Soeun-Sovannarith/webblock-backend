"""The release transition table, its conditions, new_release and Release.validate (decision 0001
section 2, decision 0011 sections 3 and 4)."""

import dataclasses
from datetime import timedelta
from typing import cast
from uuid import UUID

import pytest
from tests.unit.core.domain.builders import (
    ID_COMMAND,
    ID_PREV,
    ID_PROJECT,
    ID_RELEASE,
    ID_RELEASE2,
    ID_REQUEST,
    REPO_ID,
    SHA40,
    T0,
    T1,
    base_release,
    image_digest,
    valid_row,
)

from provisioning.core.domain import (
    NIL_ID,
    BuildResult,
    Command,
    CommandKind,
    CommandRequest,
    CommandStatus,
    DomainError,
    HostingStatus,
    IllegalTransition,
    ImmutableField,
    InvalidCommitSha,
    InvalidFailureCode,
    InvalidFailureSummary,
    InvalidId,
    InvalidImageDigest,
    InvalidRepositoryId,
    MissingFailureCode,
    MissingImageDigest,
    ProjectHosting,
    Release,
    ReleaseStatus,
    ReleaseStep,
    ReleaseStepName,
    ReleaseStepStatus,
    ReleaseTransition,
    ReplacedCandidateInput,
    RoutingStatus,
    UnknownEnumValue,
    apply_build_result,
    is_error,
    legal_release_transition,
    new_command,
    new_release,
    return_replaced_candidate,
)

# --- fixtures ------------------------------------------------------------------------------------

STEP_DEADLINE = T0 + timedelta(hours=1)

ID_STEP1 = UUID("0190a1b2-0000-7000-8000-0000000000e1")

SHA64 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

_HOSTING_KINDS = frozenset({CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING})

S = ReleaseStatus


def _build_step(step_id: UUID, attempt: int, status: ReleaseStepStatus) -> ReleaseStep:
    s = ReleaseStep(
        id=step_id,
        release_id=ID_RELEASE,
        name=ReleaseStepName.BUILD,
        attempt=attempt,
        status=status,
    )
    if status != ReleaseStepStatus.PENDING:
        s = dataclasses.replace(s, started_at=T0, deadline_at=STEP_DEADLINE)
    if status == ReleaseStepStatus.SUCCEEDED:
        s = dataclasses.replace(s, finished_at=T0)
    elif status == ReleaseStepStatus.FAILED:
        s = dataclasses.replace(s, finished_at=T0, failure_code="deadline_exceeded")
    return s


def _result_for(s: ReleaseStep) -> BuildResult:
    return BuildResult(
        project_id=ID_PROJECT,
        release_id=ID_RELEASE,
        step_id=s.id,
        attempt=s.attempt,
        git_commit_sha=SHA40,
        repository_id=REPO_ID,
        container_image_digest=image_digest("a"),
    )


def _request_for(kind: CommandKind) -> CommandRequest:
    if kind in _HOSTING_KINDS:
        return CommandRequest(
            project_id=ID_PROJECT, kind=kind, release_id=None, idempotency_key="pay-evt-42"
        )
    return CommandRequest(
        project_id=ID_PROJECT, kind=kind, release_id=ID_RELEASE, idempotency_key="key-1"
    )


def _command_in(kind: CommandKind, status: CommandStatus) -> Command:
    c = new_command(
        command_id=ID_COMMAND,
        request=_request_for(kind),
        requested_by_subject=None if kind in _HOSTING_KINDS else "user-7f3a",
        command_number=4,
        now=T0,
    )
    c = dataclasses.replace(c, status=status)
    if status in (CommandStatus.FAILED, CommandStatus.SKIPPED):
        c = dataclasses.replace(c, failure_code="earlier_failure")
    return c


def _replaced_input() -> ReplacedCandidateInput:
    hosting = ProjectHosting(
        project_id=ID_PROJECT,
        last_command_number=4,
        routing_status=RoutingStatus.STABLE,
        hosting_status=HostingStatus.ONLINE,
        updated_at=T0,
        live_release_id=ID_PREV,
        subdomain="shop",
    )
    cmd = dataclasses.replace(
        _command_in(CommandKind.DEPLOY, CommandStatus.RUNNING), release_id=ID_RELEASE2
    )
    return ReplacedCandidateInput(
        hosting=hosting, command=cmd, release=valid_row(S.CANDIDATE_READY)
    )


# The expectation table, written out by hand from decision 0001 section 2 (revision 16) and
# decision 0011 section 4 (candidate_ready -> built). Deliberately not derived from production.
_LEGAL_RELEASE_PAIRS: tuple[tuple[ReleaseStatus, ReleaseStatus], ...] = (
    (S.ACCEPTED, S.BUILDING),
    (S.ACCEPTED, S.FAILED),
    (S.BUILDING, S.BUILT),
    (S.BUILDING, S.FAILED),
    (S.BUILT, S.CANDIDATE_READY),
    (S.BUILT, S.FAILED),
    (S.CANDIDATE_READY, S.PROMOTING),
    (S.CANDIDATE_READY, S.FAILED),
    (S.CANDIDATE_READY, S.BUILT),
    (S.PROMOTING, S.LIVE),
    (S.PROMOTING, S.ROLLED_BACK),
    (S.PROMOTING, S.FAILED),
    (S.LIVE, S.SUPERSEDED),
)

_ALL_RELEASE_STATUSES = (
    S.ACCEPTED,
    S.BUILDING,
    S.BUILT,
    S.CANDIDATE_READY,
    S.PROMOTING,
    S.LIVE,
    S.SUPERSEDED,
    S.FAILED,
    S.ROLLED_BACK,
)


def _pair_ids(pairs: tuple[tuple[ReleaseStatus, ReleaseStatus], ...]) -> list[str]:
    return [f"{f}->{t}" for f, t in pairs]


def _only_through_entry_point(from_: ReleaseStatus, to: ReleaseStatus) -> bool:
    """building -> built (apply_build_result) and candidate_ready -> built
    (return_replaced_candidate): Release.transition refuses both."""
    return to == S.BUILT and from_ in (S.BUILDING, S.CANDIDATE_READY)


_FAILURE_PAIRS = tuple(p for p in _LEGAL_RELEASE_PAIRS if p[1] in (S.FAILED, S.ROLLED_BACK))
_DIRECT_PAIRS = tuple(p for p in _LEGAL_RELEASE_PAIRS if not _only_through_entry_point(*p))
_OTHER_PAIRS = tuple(p for p in _DIRECT_PAIRS if p[1] not in (S.FAILED, S.ROLLED_BACK))
_ILLEGAL_PAIRS = tuple(
    (f, t)
    for f in _ALL_RELEASE_STATUSES
    for t in _ALL_RELEASE_STATUSES
    if (f, t) not in _LEGAL_RELEASE_PAIRS
)


def _satisfying(from_: ReleaseStatus, to: ReleaseStatus) -> ReleaseTransition:
    """Inputs that satisfy every condition of Release.transition for the pair. A transition out
    of promoting to failed is a first publish (no live release); every other transition out of
    promoting names a live release that is not this one."""
    failure_code, failure_summary = "", ""
    if to in (S.FAILED, S.ROLLED_BACK):
        failure_code, failure_summary = "promotion_failed", "safe text"
    live = ID_PREV if from_ == S.PROMOTING and to != S.FAILED else None
    newer = ID_RELEASE2 if (from_, to) == (S.LIVE, S.SUPERSEDED) else None
    return ReleaseTransition(
        failure_code=failure_code,
        failure_summary=failure_summary,
        live_release_id=live,
        newer_live_release_id=newer,
    )


def _move(r: Release, from_: ReleaseStatus, to: ReleaseStatus) -> Release:
    """Perform a legal pair through its entry point."""
    if (from_, to) == (S.BUILDING, S.BUILT):
        s = _build_step(ID_STEP1, 1, ReleaseStepStatus.RUNNING)
        out, _ = apply_build_result(r, s, _result_for(s), T1)
        return out
    if (from_, to) == (S.CANDIDATE_READY, S.BUILT):
        inp = dataclasses.replace(_replaced_input(), release=r)
        return return_replaced_candidate(inp, T1)
    return r.transition(to, _satisfying(from_, to), T1)


def _rejects(
    r: Release, to: ReleaseStatus, inp: ReleaseTransition, want: type[DomainError]
) -> None:
    """A refused transition raises ``want`` and leaves the release unchanged."""
    before = dataclasses.replace(r)
    with pytest.raises(want):
        r.transition(to, inp, T1)
    assert r == before


def _ok(from_: ReleaseStatus, to: ReleaseStatus, inp: ReleaseTransition) -> None:
    out = base_release(from_).transition(to, inp, T1)
    assert out.status == to


# --- tests ------------------------------------------------------------------------------------


def test_release_status_matrix() -> None:
    assert len(_LEGAL_RELEASE_PAIRS) == 13
    count = 0
    wrong = []
    for from_ in _ALL_RELEASE_STATUSES:
        for to in _ALL_RELEASE_STATUSES:
            count += 1
            want = (from_, to) in _LEGAL_RELEASE_PAIRS
            if legal_release_transition(from_, to) != want:
                wrong.append((from_, to))
    assert count == 81
    assert wrong == []
    assert not legal_release_transition(cast(ReleaseStatus, "bogus"), S.LIVE)
    assert not legal_release_transition(S.ACCEPTED, cast(ReleaseStatus, "bogus"))


@pytest.mark.parametrize(("from_", "to"), _LEGAL_RELEASE_PAIRS, ids=_pair_ids(_LEGAL_RELEASE_PAIRS))
def test_every_legal_transition_succeeds(from_: ReleaseStatus, to: ReleaseStatus) -> None:
    out = _move(base_release(from_), from_, to)
    assert out.status == to
    assert out.updated_at == T1


def test_building_to_built_only_through_build_result() -> None:
    """building -> built is reached only through the build-result check (decision 0001 sec. 2)."""
    _rejects(
        base_release(S.BUILDING),
        S.BUILT,
        ReleaseTransition(container_image_digest=image_digest("a")),
        IllegalTransition,
    )
    _rejects(base_release(S.BUILDING), S.BUILT, ReleaseTransition(), IllegalTransition)


@pytest.mark.parametrize(("from_", "to"), _ILLEGAL_PAIRS, ids=_pair_ids(_ILLEGAL_PAIRS))
def test_illegal_transitions_rejected_and_unchanged(
    from_: ReleaseStatus, to: ReleaseStatus
) -> None:
    inp = ReleaseTransition(
        failure_code="x",
        container_image_digest=image_digest("a"),
        newer_live_release_id=ID_RELEASE2,
        live_release_id=ID_PREV,
    )
    _rejects(valid_row(from_), to, inp, IllegalTransition)


def test_transition_to_unknown_status() -> None:
    _rejects(
        base_release(S.ACCEPTED),
        cast(ReleaseStatus, "bogus"),
        ReleaseTransition(),
        IllegalTransition,
    )


def test_terminal_states_have_no_exit() -> None:
    for from_ in (S.SUPERSEDED, S.FAILED, S.ROLLED_BACK):
        for to in ReleaseStatus:
            assert not legal_release_transition(from_, to), f"{from_} -> {to} is legal"


@pytest.mark.parametrize(("from_", "to"), _FAILURE_PAIRS, ids=_pair_ids(_FAILURE_PAIRS))
def test_failure_code_required(from_: ReleaseStatus, to: ReleaseStatus) -> None:
    assert len(_FAILURE_PAIRS) == 6
    for code in ("", "   "):
        inp = dataclasses.replace(_satisfying(from_, to), failure_code=code)
        _rejects(base_release(from_), to, inp, MissingFailureCode)


def test_failure_code_format() -> None:
    for code in ("a", "build_failed", "a1_b2", "z" + "9" * 99):
        out = base_release(S.BUILDING).transition(
            S.FAILED, ReleaseTransition(failure_code=code), T1
        )
        assert out.failure_code == code
    # Malformed is not reported as missing and missing is not reported as malformed.
    r = base_release(S.BUILDING)
    with pytest.raises(DomainError) as malformed:
        r.transition(S.FAILED, ReleaseTransition(failure_code="Bad"), T1)
    assert not is_error(malformed.value, MissingFailureCode)
    with pytest.raises(DomainError) as absent:
        r.transition(S.FAILED, ReleaseTransition(), T1)
    assert not is_error(absent.value, InvalidFailureCode)


_INVALID_CODES = {
    "uppercase": "Build",
    "leading digit": "1abc",
    "leading underscore": "_a",
    "hyphen": "a-b",
    "inner space": "a b",
    "leading space": " a",
    "trailing space": "a ",
    "newline": "line1\nline2",
    "nul": "a\x00",
    "non ascii": "café",
    "101 characters": "a" + "b" * 100,
    "markup": "<script>",
}


@pytest.mark.parametrize("code", list(_INVALID_CODES.values()), ids=list(_INVALID_CODES))
def test_failure_code_format_invalid(code: str) -> None:
    _rejects(
        base_release(S.BUILDING),
        S.FAILED,
        ReleaseTransition(failure_code=code),
        InvalidFailureCode,
    )
    inp = dataclasses.replace(_satisfying(S.PROMOTING, S.ROLLED_BACK), failure_code=code)
    _rejects(base_release(S.PROMOTING), S.ROLLED_BACK, inp, InvalidFailureCode)


_VALID_SUMMARIES = {
    "500 ascii": "x" * 500,
    "500 characters": "é" * 500,
    "plain": "compile error in page 3",
    # Letters, marks and digits of any script are ordinary text (decision 0001 section 9).
    "accented": "échec de compilation à la page 3 : symbole non défini",
    "khmer": "កំហុសក្នុងការចងក្រងទំព័រទី៣",
    "japanese": "ページ3のビルドに失敗しました",
    "arabic": "فشل بناء الصفحة ٣",
    # Format characters other than the bidirectional controls stay legal.
    "khmer with zero width spaces": "កំហុស\u200bក្នុង\u200bការ\u200bចងក្រង\u200bទំព័រ\u200bទី\u200b៣",
    "persian with zero width non-joiner": "ساخت صفحه انجام نمی\u200cشود",
    "emoji zero width joiner sequence": "build failed \U0001f468\u200d\U0001f4bb",
    "soft hyphen": "co\u00adoperate",
    # A summary made only of legal format characters is not blank (decision 0001 section 9).
    "only zero width spaces": "\u200b\u200b",
    # Only a summary of nothing but whitespace is blank; spaces around or inside text are kept.
    "surrounding spaces": "  compile error  ",
    "no-break space inside": "page\u00a03",
}
# The assigned code points next to each forbidden bidirectional control stay legal.
for _cp in (0x061B, 0x061D, 0x200D, 0x2010, 0x202F, 0x206A):
    _VALID_SUMMARIES[f"neighbor of a bidirectional control U+{_cp:04X}"] = "page" + chr(_cp) + "3"


@pytest.mark.parametrize("summary", list(_VALID_SUMMARIES.values()), ids=list(_VALID_SUMMARIES))
def test_failure_summary_rules_valid(summary: str) -> None:
    out = base_release(S.BUILDING).transition(
        S.FAILED, ReleaseTransition(failure_code="c", failure_summary=summary), T1
    )
    assert out.failure_summary == summary


# Every bidirectional control of decision 0001 section 9, written as code points.
_BIDI_CONTROLS = (
    0x061C,
    0x200E,
    0x200F,
    0x202A,
    0x202B,
    0x202C,
    0x202D,
    0x202E,
    0x2066,
    0x2067,
    0x2068,
    0x2069,
)
_INVALID_SUMMARIES = {
    "501 characters": "x" * 501,
    "501 multibyte": "é" * 501,
    "1 MiB": "x" * (1 << 20),
    "newline": "line1\nline2",
    "tab": "a\tb",
    "nul": "a\x00b",
    "del": "a\x7fb",
    "c1 control": "a\u0085b",
    # The byte string "a\xffb" is not UTF-8; a Python str holds the byte as the lone
    # surrogate U+DCFF (surrogateescape), which is not valid UTF-8 either.
    "invalid utf-8": "a\udcffb",
    # Line and paragraph separators (Zl, Zp).
    "line separator": "line1\u2028line2",
    "paragraph separator": "line1\u2029line2",
    # Present but blank.
    "three spaces": "   ",
    "only no-break spaces": "\u00a0\u3000",
}
for _cp in _BIDI_CONTROLS:
    _INVALID_SUMMARIES[f"bidirectional control U+{_cp:04X}"] = "report" + chr(_cp) + "txt.exe"


@pytest.mark.parametrize("summary", list(_INVALID_SUMMARIES.values()), ids=list(_INVALID_SUMMARIES))
def test_failure_summary_rules_invalid(summary: str) -> None:
    assert len(_BIDI_CONTROLS) == 12
    _rejects(
        base_release(S.BUILDING),
        S.FAILED,
        ReleaseTransition(failure_code="c", failure_summary=summary),
        InvalidFailureSummary,
    )


@pytest.mark.parametrize(("from_", "to"), _FAILURE_PAIRS, ids=_pair_ids(_FAILURE_PAIRS))
def test_failure_records_code_and_summary(from_: ReleaseStatus, to: ReleaseStatus) -> None:
    inp = dataclasses.replace(
        _satisfying(from_, to),
        failure_code="candidate_failed",
        failure_summary="health check failed",
    )
    out = base_release(from_).transition(to, inp, T1)
    assert out.failure_code == "candidate_failed"
    assert out.failure_summary == "health check failed"
    # An empty summary stays NULL.
    out2 = base_release(from_).transition(to, dataclasses.replace(inp, failure_summary=""), T1)
    assert out2.failure_summary is None


@pytest.mark.parametrize(("from_", "to"), _OTHER_PAIRS, ids=_pair_ids(_OTHER_PAIRS))
def test_failure_fields_refused_on_other_transitions(
    from_: ReleaseStatus, to: ReleaseStatus
) -> None:
    """A failure code or summary is present exactly on failed and rolled_back
    (releases_failure_code_matches_status), so supplying one elsewhere is refused."""
    inp = dataclasses.replace(_satisfying(from_, to), failure_code="promotion_failed")
    _rejects(base_release(from_), to, inp, InvalidFailureCode)
    inp = dataclasses.replace(_satisfying(from_, to), failure_summary="text")
    _rejects(base_release(from_), to, inp, InvalidFailureSummary)


_DIGEST_CASES = [
    (from_, to, recorded, supplied)
    for (from_, to) in _DIRECT_PAIRS
    for recorded in (False, True)
    for supplied in (image_digest("a"), "")
]


@pytest.mark.parametrize(
    ("from_", "to", "recorded", "supplied"),
    _DIGEST_CASES,
    ids=[f'{f}->{t}/recorded={str(r).lower()}/supplied="{s}"' for f, t, r, s in _DIGEST_CASES],
)
def test_digest_supplied_outside_recording_transition_rejected(
    from_: ReleaseStatus, to: ReleaseStatus, recorded: bool, supplied: str
) -> None:
    assert len(_DIRECT_PAIRS) == 11
    r = base_release(from_)
    if recorded:
        r = dataclasses.replace(r, container_image_digest=image_digest("a"))
    # Any value other than None counts as supplied, even the recorded value or "".
    inp = dataclasses.replace(_satisfying(from_, to), container_image_digest=supplied)
    _rejects(r, to, inp, ImmutableField)


_BAD_DIGESTS = ("sha256:xyz", "registry.example/x:latest", "sha256:" + "A" * 64)
_BAD_DIGEST_CASES = [(bad, f, t) for bad in _BAD_DIGESTS for (f, t) in _DIRECT_PAIRS]


@pytest.mark.parametrize(
    ("bad", "from_", "to"),
    _BAD_DIGEST_CASES,
    ids=[f"{bad}/{f}->{t}" for bad, f, t in _BAD_DIGEST_CASES],
)
def test_invalid_supplied_digest_rejected(
    bad: str, from_: ReleaseStatus, to: ReleaseStatus
) -> None:
    """A non-empty digest that does not parse is InvalidImageDigest on every transition, before
    ImmutableField."""
    inp = dataclasses.replace(_satisfying(from_, to), container_image_digest=bad)
    _rejects(base_release(from_), to, inp, InvalidImageDigest)


def test_promoting_exits_depend_on_active_release() -> None:
    def fail(inp: ReleaseTransition) -> ReleaseTransition:
        return dataclasses.replace(inp, failure_code="promotion_failed")

    with_live = ReleaseTransition(live_release_id=ID_PREV)
    none = ReleaseTransition()

    # With a live release the only failure exit is rolled_back.
    _ok(S.PROMOTING, S.ROLLED_BACK, fail(with_live))
    _ok(S.PROMOTING, S.LIVE, with_live)
    _rejects(base_release(S.PROMOTING), S.FAILED, fail(with_live), IllegalTransition)

    # Without one (first publish) the only failure exit is failed.
    _ok(S.PROMOTING, S.FAILED, fail(none))
    _ok(S.PROMOTING, S.LIVE, none)
    _rejects(base_release(S.PROMOTING), S.ROLLED_BACK, fail(none), IllegalTransition)

    # The live release is never the promoting release itself, and the nil UUID is no identity.
    for to in (S.LIVE, S.ROLLED_BACK, S.FAILED):

        def inp(live: UUID, to: ReleaseStatus = to) -> ReleaseTransition:
            t = ReleaseTransition(live_release_id=live)
            return t if to == S.LIVE else fail(t)

        _rejects(base_release(S.PROMOTING), to, inp(ID_RELEASE), IllegalTransition)
        _rejects(base_release(S.PROMOTING), to, inp(NIL_ID), InvalidId)


def test_promoted_at_only_on_promoting_to_live() -> None:
    r = base_release(S.PROMOTING).transition(S.LIVE, ReleaseTransition(), T1)
    assert r.promoted_at == T1
    # live -> superseded keeps the promotion time.
    s = valid_row(S.LIVE).transition(S.SUPERSEDED, _satisfying(S.LIVE, S.SUPERSEDED), T1)
    assert s.promoted_at == T0
    # No other transition sets it; rolled_back in particular was never confirmed live.
    for from_, to in _LEGAL_RELEASE_PAIRS:
        if (from_, to) == (S.PROMOTING, S.LIVE) or from_ == S.LIVE:
            continue
        o = _move(base_release(from_), from_, to)
        assert o.promoted_at is None, f"{from_}->{to} set promoted_at"


def test_supersede_requires_newer_live_release() -> None:
    r = valid_row(S.LIVE)
    _rejects(r, S.SUPERSEDED, ReleaseTransition(), IllegalTransition)
    _rejects(r, S.SUPERSEDED, ReleaseTransition(newer_live_release_id=r.id), IllegalTransition)
    _rejects(r, S.SUPERSEDED, ReleaseTransition(newer_live_release_id=NIL_ID), IllegalTransition)
    out = r.transition(S.SUPERSEDED, ReleaseTransition(newer_live_release_id=ID_RELEASE2), T1)
    assert out.status == S.SUPERSEDED


@pytest.mark.parametrize(("from_", "to"), _LEGAL_RELEASE_PAIRS, ids=_pair_ids(_LEGAL_RELEASE_PAIRS))
def test_immutable_fields_untouched(from_: ReleaseStatus, to: ReleaseStatus) -> None:
    before = base_release(from_)
    out = _move(before, from_, to)
    assert out.id == before.id
    assert out.project_id == before.project_id
    assert out.generation_request_id == before.generation_request_id
    assert out.repository_id == before.repository_id
    assert out.git_commit_sha == before.git_commit_sha
    assert out.created_at == before.created_at


_GOOD_NEW_RELEASE: dict[str, object] = {
    "release_id": ID_RELEASE,
    "project_id": ID_PROJECT,
    "generation_request_id": ID_REQUEST,
    "repository_id": REPO_ID,
    "git_commit_sha": SHA64,
}


def _new_release(**overrides: object) -> Release:
    p = {**_GOOD_NEW_RELEASE, **overrides}
    return new_release(
        release_id=cast(UUID, p["release_id"]),
        project_id=cast(UUID, p["project_id"]),
        generation_request_id=cast(UUID, p["generation_request_id"]),
        repository_id=cast(int, p["repository_id"]),
        git_commit_sha=cast(str, p["git_commit_sha"]),
        now=T0,
    )


def test_new_release() -> None:
    r = _new_release()
    want = Release(
        id=ID_RELEASE,
        project_id=ID_PROJECT,
        generation_request_id=ID_REQUEST,
        repository_id=REPO_ID,
        git_commit_sha=SHA64,
        status=S.ACCEPTED,
        created_at=T0,
        updated_at=T0,
    )
    assert r == want
    r.validate()


_NEW_RELEASE_REFUSALS: dict[str, tuple[dict[str, object], type[DomainError]]] = {
    "zero release": ({"release_id": NIL_ID}, InvalidId),
    "zero project": ({"project_id": NIL_ID}, InvalidId),
    "zero request": ({"generation_request_id": NIL_ID}, InvalidId),
    "repository 0": ({"repository_id": 0}, InvalidRepositoryId),
    "repository -1": ({"repository_id": -1}, InvalidRepositoryId),
    "repository 2^53": ({"repository_id": 1 << 53}, InvalidRepositoryId),
    "empty commit": ({"git_commit_sha": ""}, InvalidCommitSha),
    "branch commit": ({"git_commit_sha": "main"}, InvalidCommitSha),
    "short commit": ({"git_commit_sha": "0123456"}, InvalidCommitSha),
}


@pytest.mark.parametrize(
    ("overrides", "want"), list(_NEW_RELEASE_REFUSALS.values()), ids=list(_NEW_RELEASE_REFUSALS)
)
def test_new_release_refused(overrides: dict[str, object], want: type[DomainError]) -> None:
    with pytest.raises(want):
        _new_release(**overrides)


def test_release_validate() -> None:
    """Release.validate checks a row loaded from storage against the formats and the constraints
    of decision 0004 section 9."""
    for status in ReleaseStatus:
        valid_row(status).validate()
    # failed may hold a digest (it can fail after built) or none.
    dataclasses.replace(valid_row(S.FAILED), container_image_digest=image_digest("a")).validate()
    dataclasses.replace(valid_row(S.FAILED), failure_summary="safe text").validate()


_VALIDATE_REFUSALS: dict[str, tuple[ReleaseStatus, dict[str, object], type[DomainError]]] = {
    "zero id": (S.LIVE, {"id": NIL_ID}, InvalidId),
    "zero project": (S.LIVE, {"project_id": NIL_ID}, InvalidId),
    "zero request": (S.LIVE, {"generation_request_id": NIL_ID}, InvalidId),
    "repository 0": (S.LIVE, {"repository_id": 0}, InvalidRepositoryId),
    "repository above max": (S.LIVE, {"repository_id": 1 << 53}, InvalidRepositoryId),
    "commit branch": (S.LIVE, {"git_commit_sha": "main"}, InvalidCommitSha),
    "commit uppercase": (S.LIVE, {"git_commit_sha": "A" * 40}, InvalidCommitSha),
    "unknown status": (S.LIVE, {"status": "frozen"}, UnknownEnumValue),
    "digest malformed": (S.LIVE, {"container_image_digest": "x:latest"}, InvalidImageDigest),
    "digest empty": (S.LIVE, {"container_image_digest": ""}, InvalidImageDigest),
    "code malformed": (S.FAILED, {"failure_code": "Bad"}, InvalidFailureCode),
    "summary malformed": (S.FAILED, {"failure_summary": "a\x00"}, InvalidFailureSummary),
    "summary empty string": (S.FAILED, {"failure_summary": ""}, InvalidFailureSummary),
    "code empty on failed": (S.FAILED, {"failure_code": ""}, MissingFailureCode),
    "failed without code": (S.FAILED, {"failure_code": None}, MissingFailureCode),
    "rolled_back no code": (S.ROLLED_BACK, {"failure_code": None}, MissingFailureCode),
    "live with code": (S.LIVE, {"failure_code": "x"}, InvalidFailureCode),
    "live with summary": (S.LIVE, {"failure_summary": "x"}, InvalidFailureSummary),
    "accepted with code": (S.ACCEPTED, {"failure_code": "x"}, InvalidFailureCode),
    "live without promoted": (S.LIVE, {"promoted_at": None}, IllegalTransition),
    "superseded no promote": (S.SUPERSEDED, {"promoted_at": None}, IllegalTransition),
    "rolled_back promoted": (S.ROLLED_BACK, {"promoted_at": T0}, IllegalTransition),
    "promoting promoted": (S.PROMOTING, {"promoted_at": T0}, IllegalTransition),
}
# releases_no_digest_before_built and releases_digest_after_build.
for _s in (S.ACCEPTED, S.BUILDING):
    _VALIDATE_REFUSALS[f"digest while {_s}"] = (
        _s,
        {"container_image_digest": image_digest("a")},
        InvalidImageDigest,
    )
for _s in (S.BUILT, S.CANDIDATE_READY, S.PROMOTING, S.LIVE, S.SUPERSEDED, S.ROLLED_BACK):
    _VALIDATE_REFUSALS[f"no digest while {_s}"] = (
        _s,
        {"container_image_digest": None},
        MissingImageDigest,
    )


@pytest.mark.parametrize(
    ("status", "changes", "want"),
    list(_VALIDATE_REFUSALS.values()),
    ids=list(_VALIDATE_REFUSALS),
)
def test_release_validate_refused(
    status: ReleaseStatus, changes: dict[str, object], want: type[DomainError]
) -> None:
    r = dataclasses.replace(valid_row(status), **changes)  # type: ignore[arg-type]  # field values vary by case
    with pytest.raises(want):
        r.validate()
