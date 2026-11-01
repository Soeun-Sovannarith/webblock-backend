package org.kshrd.webblock.project.application.project.handler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.time.Duration;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.function.Consumer;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.kshrd.webblock.project.application.project.model.NewProject;
import org.kshrd.webblock.project.application.project.model.ProjectOwnership;
import org.kshrd.webblock.project.application.project.model.ProjectPage;
import org.kshrd.webblock.project.application.project.model.ProjectRecord;
import org.kshrd.webblock.project.application.project.model.SubdomainAvailability;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import org.kshrd.webblock.project.domain.project.model.ProjectStatus;
import org.kshrd.webblock.project.infrastructure.config.project.ProjectConfigImport;
import org.kshrd.webblock.project.support.DatabaseErrors;
import org.kshrd.webblock.project.support.JpaSliceTest;
import org.kshrd.webblock.project.support.MutableClock;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Import;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.support.TransactionTemplate;

@JpaSliceTest
@Import({ProjectService.class, ProjectConfigImport.class})
class ProjectServiceTest {

    @Autowired
    ProjectService projects;

    @Autowired
    TransactionTemplate tx;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    MutableClock clock;

    private ProjectRecord create(UUID owner, String name) {
        UUID id = Uuids.v7();
        return tx.execute(s -> projects.create(newProject(id, owner, name, TestSubdomains.of(id))));
    }

    private static NewProject newProject(UUID id, UUID owner, String name, String subdomain) {
        return new NewProject(id, owner, name, subdomain, UUID.randomUUID(), "0.1.0");
    }

    private static void invalidSubdomain(Throwable e) {
        assertThat(((WebBlockException) e).errorCode()).isEqualTo(ErrorCode.VALIDATION_FAILED);
        assertThat(((WebBlockException) e).details()).singleElement().satisfies(d -> {
            assertThat(d.field()).isEqualTo("subdomain");
            assertThat(d.code()).isEqualTo("VALUE_INVALID");
        });
    }

    private static Consumer<Throwable> code(ErrorCode expected) {
        return e -> assertThat(((WebBlockException) e).errorCode()).isEqualTo(expected);
    }

    @Test
    @DisplayName("FR-13.1: a new project stores its sub-domain; the hostname is <subdomain>.<site domain>")
    void fr13_1_createsADraftProjectWithItsSubdomain() {
        UUID owner = UUID.randomUUID();
        UUID id = Uuids.v7();
        String subdomain = "kiln" + id.toString().substring(24);
        ProjectRecord created = tx.execute(s -> projects.create(newProject(id, owner, "Kiln Coffee", subdomain)));
        assertThat(created.status()).isEqualTo(ProjectStatus.draft);
        assertThat(created.editRevision()).isZero();
        assertThat(created.subdomain()).isEqualTo(subdomain);
        assertThat(created.platformHostname()).isEqualTo(subdomain + ".sites.webblock.test");
        assertThat(jdbc.queryForObject("SELECT subdomain FROM project.projects WHERE project_id = ?", String.class, id))
                .isEqualTo(subdomain);
        assertThat(projects.find(id)).contains(created);
        assertThat(projects.getOwned(id, owner)).isEqualTo(created);
        ProjectRecord locked = tx.execute(s -> projects.lockOwned(id, owner));
        assertThat(locked).isEqualTo(created);
        assertThat(projects.listOwned(owner, 10, null, null, null).items()).containsExactly(created);
    }

    @Test
    @DisplayName("FR-13.3: auth, -shop and ab are VALIDATION_FAILED, field subdomain VALUE_INVALID")
    void fr13_3_aReservedOrMalformedSubdomainIsRefused() {
        for (String bad : new String[] {"auth", "-shop", "ab", "www", "Kiln", ""}) {
            assertThatThrownBy(() -> projects.requireValidSubdomain(bad))
                    .as(bad)
                    .satisfies(ProjectServiceTest::invalidSubdomain);
        }
        assertThatThrownBy(() -> projects.requireValidSubdomain(null)).satisfies(ProjectServiceTest::invalidSubdomain);
        projects.requireValidSubdomain("kilncoffee");
    }

    @Test
    @DisplayName("FR-13.3: create refuses a sub-domain the rule refuses and writes nothing")
    void fr13_3_createRefusesAnInvalidSubdomain() {
        UUID id = Uuids.v7();
        assertThatThrownBy(() -> tx.execute(s -> projects.create(newProject(id, UUID.randomUUID(), "x", "auth"))))
                .satisfies(ProjectServiceTest::invalidSubdomain);
        assertThat(projects.find(id)).isEmpty();
    }

    @Test
    @DisplayName("FR-13.2: a sub-domain is taken while any project holds it, a deleted one included")
    void fr13_2_aDeletedProjectStillHoldsItsSubdomain() {
        ProjectRecord gone = create(UUID.randomUUID(), "Gone");
        jdbc.update("UPDATE project.projects SET deleted_at = now() WHERE project_id = ?", gone.projectId());
        assertThat(projects.subdomainTaken(gone.subdomain())).isTrue();
        assertThat(projects.subdomainTaken("free-" + gone.projectId().toString().substring(24)))
                .isFalse();
    }

    @Test
    @DisplayName("FR-13.2: a second project with a held sub-domain is refused by projects_subdomain_key")
    void fr13_2_theUniqueKeyDecidesARace() {
        ProjectRecord first = create(UUID.randomUUID(), "First");
        UUID second = Uuids.v7();
        assertThatThrownBy(() -> tx.execute(
                        s -> projects.create(newProject(second, UUID.randomUUID(), "Second", first.subdomain()))))
                .isInstanceOf(DataIntegrityViolationException.class)
                .satisfies(e -> assertThat(DatabaseErrors.constraintName(e)).contains("projects_subdomain_key"));
        assertThat(projects.find(second)).isEmpty();
    }

    @Test
    @DisplayName("FR-13.13: a valid sub-domain no project holds is AVAILABLE")
    void fr13_13_aFreeSubdomainIsAvailable() {
        String free = "free-" + Uuids.v7().toString().substring(24);
        assertThat(projects.checkSubdomain(free)).isEqualTo(SubdomainAvailability.AVAILABLE);
    }

    @Test
    @DisplayName("FR-13.14: a sub-domain held by a live or a deleted project is TAKEN, the same answer for both")
    void fr13_14_aLiveOrDeletedHolderMakesItTaken() {
        ProjectRecord live = create(UUID.randomUUID(), "Live");
        ProjectRecord gone = create(UUID.randomUUID(), "Gone");
        jdbc.update("UPDATE project.projects SET deleted_at = now() WHERE project_id = ?", gone.projectId());
        assertThat(projects.checkSubdomain(live.subdomain())).isEqualTo(SubdomainAvailability.TAKEN);
        assertThat(projects.checkSubdomain(gone.subdomain())).isEqualTo(SubdomainAvailability.TAKEN);
    }

    @Test
    @DisplayName("FR-13.15: ab, -shop, a 64-character label, auth, an upper-case label and null are INVALID")
    void fr13_15_aLabelTheRuleRefusesIsInvalid() {
        for (String bad : new String[] {
            "ab", "-shop", "a".repeat(64), "a".repeat(56), "kiln-preview", "xn--kiln", "preview", "auth", "Kiln", ""
        }) {
            assertThat(projects.checkSubdomain(bad)).as(bad).isEqualTo(SubdomainAvailability.INVALID);
        }
        assertThat(projects.checkSubdomain(null)).isEqualTo(SubdomainAvailability.INVALID);
    }

    @Test
    @DisplayName("FR-13.15: a reserved name is INVALID, not TAKEN")
    void fr13_15_aReservedNameIsInvalidNotTaken() {
        assertThat(projects.checkSubdomain("www")).isEqualTo(SubdomainAvailability.INVALID);
    }

    @ParameterizedTest
    @ValueSource(
            strings = {
                "kilncoffee",
                "abc",
                "kiln-coffee-2",
                "0shop",
                "ab",
                "-shop",
                "shop-",
                "kiln_coffee",
                "kiln.coffee",
                "Kiln",
                "auth",
                "www",
                "preview",
                "webblock",
                "kiln-preview",
                "xn--kiln",
                "previewshop",
                "xnshop",
                "",
                " kilncoffee",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            })
    @DisplayName("FR-13.17: the check and create apply one rule: INVALID exactly when create answers VALUE_INVALID")
    void fr13_17_theCheckAndCreateAgree(String label) {
        boolean createRefuses;
        try {
            projects.requireValidSubdomain(label);
            createRefuses = false;
        } catch (WebBlockException e) {
            invalidSubdomain(e);
            createRefuses = true;
        }
        assertThat(projects.checkSubdomain(label) == SubdomainAvailability.INVALID)
                .as(label)
                .isEqualTo(createRefuses);
    }

    @Test
    void ownershipIsTheOwnerAndTheReadOnlyState() {
        UUID owner = UUID.randomUUID();
        ProjectRecord project = create(owner, "Owned");
        assertThat(projects.ownership(project.projectId())).contains(new ProjectOwnership(owner, false));
        jdbc.update("UPDATE project.projects SET status = 'suspended' WHERE project_id = ?", project.projectId());
        assertThat(projects.ownership(project.projectId())).contains(new ProjectOwnership(owner, true));
        jdbc.update("UPDATE project.projects SET deleted_at = now() WHERE project_id = ?", project.projectId());
        assertThat(projects.ownership(project.projectId()))
                .as("a deleted project is absent")
                .isEmpty();
        assertThat(projects.ownership(UUID.randomUUID())).isEmpty();
    }

    @Test
    void creatingAnExistingIdFailsInsteadOfOverwriting() {
        ProjectRecord first = create(UUID.randomUUID(), "Original");
        assertThatThrownBy(() -> tx.execute(s -> projects.create(
                        newProject(first.projectId(), UUID.randomUUID(), "Intruder", "intruder-" + Uuids.v7()))))
                .isInstanceOf(DataIntegrityViolationException.class)
                .satisfies(e -> assertThat(DatabaseErrors.constraintName(e)).contains("projects_pkey"));
        assertThat(projects.find(first.projectId()).orElseThrow().name()).isEqualTo("Original");
    }

    @Test
    void createNeedsTheCallersTransaction() {
        assertThatThrownBy(() -> projects.create(newProject(Uuids.v7(), UUID.randomUUID(), "x", "no-transaction")))
                .isInstanceOf(org.springframework.transaction.IllegalTransactionStateException.class);
    }

    @Test
    void anotherOwnerSeesNotFound() {
        ProjectRecord project = create(UUID.randomUUID(), "Mine");
        assertThatThrownBy(() -> projects.getOwned(project.projectId(), UUID.randomUUID()))
                .satisfies(code(ErrorCode.PROJECT_NOT_FOUND));
        assertThatThrownBy(() -> projects.getOwned(UUID.randomUUID(), project.ownerUserId()))
                .satisfies(code(ErrorCode.PROJECT_NOT_FOUND));
    }

    @Test
    void listsNewestFirstInPagesAndSkipsDeletedProjects() {
        UUID owner = UUID.randomUUID();
        ProjectRecord a = create(owner, "a");
        clock.advance(Duration.ofSeconds(1));
        ProjectRecord b = create(owner, "b");
        clock.advance(Duration.ofSeconds(1));
        ProjectRecord c = create(owner, "c");
        clock.advance(Duration.ofSeconds(1));
        ProjectRecord gone = create(owner, "gone");
        jdbc.update("UPDATE project.projects SET deleted_at = now() WHERE project_id = ?", gone.projectId());
        create(UUID.randomUUID(), "someone else's");

        ProjectPage first = projects.listOwned(owner, 2, null, null, null);
        assertThat(first.items()).extracting(ProjectRecord::projectId).containsExactly(c.projectId(), b.projectId());
        assertThat(first.nextCursor()).isNotNull();
        ProjectPage second = projects.listOwned(owner, 2, first.nextCursor(), null, null);
        assertThat(second.items()).extracting(ProjectRecord::projectId).containsExactly(a.projectId());
        assertThat(second.nextCursor()).isNull();
    }

    private static List<String> names(ProjectPage page) {
        return page.items().stream().map(ProjectRecord::name).toList();
    }

    @Test
    @DisplayName("FR-14.5: q matches part of a name, ignoring case and surrounding spaces")
    void fr14_5_searchMatchesPartOfTheNameIgnoringCase() {
        UUID owner = UUID.randomUUID();
        create(owner, "Kiln Coffee");
        create(owner, "Bakery");
        create(owner, "Iron Kiln Works");
        assertThat(names(projects.listOwned(owner, 10, null, "  kILN ", null)))
                .containsExactlyInAnyOrder("Kiln Coffee", "Iron Kiln Works");
        assertThat(names(projects.listOwned(owner, 10, null, "   ", null))).hasSize(3);
        assertThat(names(projects.listOwned(owner, 10, null, "zzz", null))).isEmpty();
    }

    @Test
    @DisplayName("FR-14.5: % and _ in q are literal characters, not wildcards")
    void fr14_5_wildcardsInTheSearchAreLiteral() {
        UUID owner = UUID.randomUUID();
        create(owner, "100% Organic");
        create(owner, "Fresh_Bakes");
        create(owner, "Plain Shop");
        create(owner, "Back\\slash");
        assertThat(names(projects.listOwned(owner, 10, null, "%", null))).containsExactly("100% Organic");
        assertThat(names(projects.listOwned(owner, 10, null, "h_b", null))).containsExactly("Fresh_Bakes");
        assertThat(names(projects.listOwned(owner, 10, null, "_", null))).containsExactly("Fresh_Bakes");
        assertThat(names(projects.listOwned(owner, 10, null, "\\", null))).containsExactly("Back\\slash");
    }

    @Test
    @DisplayName("FR-14.5: a q longer than 100 characters is a validation error")
    void fr14_5_aTooLongSearchIsRefused() {
        assertThatThrownBy(() -> projects.listOwned(UUID.randomUUID(), 10, null, "x".repeat(101), null))
                .satisfies(code(ErrorCode.VALIDATION_FAILED));
        assertThat(projects.listOwned(UUID.randomUUID(), 10, null, "x".repeat(100), null)
                        .items())
                .isEmpty();
    }

    @Test
    @DisplayName("FR-14.6: status keeps one status; no status keeps all; deleted and strangers' stay out")
    void fr14_6_statusFilter() {
        UUID owner = UUID.randomUUID();
        ProjectRecord draft = create(owner, "Draft one");
        ProjectRecord active = create(owner, "Active one");
        ProjectRecord suspended = create(owner, "Suspended one");
        ProjectRecord deletedActive = create(owner, "Deleted active");
        ProjectRecord strangers = create(UUID.randomUUID(), "Stranger active");
        jdbc.update("UPDATE project.projects SET status = 'active' WHERE project_id = ?", active.projectId());
        jdbc.update("UPDATE project.projects SET status = 'suspended' WHERE project_id = ?", suspended.projectId());
        jdbc.update("UPDATE project.projects SET status = 'active' WHERE project_id = ?", deletedActive.projectId());
        jdbc.update("UPDATE project.projects SET deleted_at = now() WHERE project_id = ?", deletedActive.projectId());
        jdbc.update("UPDATE project.projects SET status = 'active' WHERE project_id = ?", strangers.projectId());

        assertThat(names(projects.listOwned(owner, 10, null, null, "draft"))).containsExactly(draft.name());
        assertThat(names(projects.listOwned(owner, 10, null, null, "active"))).containsExactly(active.name());
        assertThat(names(projects.listOwned(owner, 10, null, null, "suspended")))
                .containsExactly(suspended.name());
        assertThat(names(projects.listOwned(owner, 10, null, null, null))).hasSize(3);
    }

    @Test
    @DisplayName("FR-14.6: an unknown status is a validation error")
    void fr14_6_anUnknownStatusIsRefused() {
        for (String bad : new String[] {"deleted", "ACTIVE", ""}) {
            assertThatThrownBy(() -> projects.listOwned(UUID.randomUUID(), 10, null, null, bad))
                    .satisfies(code(ErrorCode.VALIDATION_FAILED));
        }
    }

    @Test
    @DisplayName("FR-14.7: q, status and the cursor combine; the second page keeps the same filters")
    void fr14_7_searchAndStatusPageTogether() {
        UUID owner = UUID.randomUUID();
        for (int i = 1; i <= 3; i++) {
            ProjectRecord match = create(owner, "Cafe " + i);
            jdbc.update("UPDATE project.projects SET status = 'active' WHERE project_id = ?", match.projectId());
            clock.advance(Duration.ofSeconds(1));
        }
        create(owner, "Cafe draft");
        clock.advance(Duration.ofSeconds(1));
        ProjectRecord other = create(owner, "Bakery");
        jdbc.update("UPDATE project.projects SET status = 'active' WHERE project_id = ?", other.projectId());
        create(UUID.randomUUID(), "Cafe stranger");

        ProjectPage first = projects.listOwned(owner, 2, null, "cafe", "active");
        assertThat(names(first)).containsExactly("Cafe 3", "Cafe 2");
        assertThat(first.nextCursor()).isNotNull();
        ProjectPage second = projects.listOwned(owner, 2, first.nextCursor(), "cafe", "active");
        assertThat(names(second)).containsExactly("Cafe 1");
        assertThat(second.nextCursor()).isNull();
    }

    @Test
    void aGarbageCursorIsAValidationError() {
        assertThatThrownBy(() -> projects.listOwned(UUID.randomUUID(), 10, "not-a-cursor", null, null))
                .satisfies(code(ErrorCode.VALIDATION_FAILED));
    }

    @Test
    @DisplayName("FR-39.4, FR-38.3: a suspended project is read-only; a deleted one is not found")
    void aSuspendedProjectIsReadOnlyAndADeletedOneIsNotFound() {
        ProjectRecord suspended = create(UUID.randomUUID(), "S");
        jdbc.update("UPDATE project.projects SET status = 'suspended' WHERE project_id = ?", suspended.projectId());
        assertThat(projects.getOwned(suspended.projectId(), suspended.ownerUserId())
                        .readOnly())
                .isTrue();
        assertThatThrownBy(() -> tx.execute(
                        s -> projects.advanceEditRevision(suspended.projectId(), suspended.ownerUserId(), 0)))
                .satisfies(code(ErrorCode.PROJECT_READ_ONLY));

        ProjectRecord deleted = create(UUID.randomUUID(), "D");
        jdbc.update("UPDATE project.projects SET deleted_at = now() WHERE project_id = ?", deleted.projectId());
        assertThatThrownBy(() -> projects.getOwned(deleted.projectId(), deleted.ownerUserId()))
                .satisfies(code(ErrorCode.PROJECT_NOT_FOUND));
        assertThatThrownBy(() ->
                        tx.execute(s -> projects.advanceEditRevision(deleted.projectId(), deleted.ownerUserId(), 0)))
                .satisfies(code(ErrorCode.PROJECT_NOT_FOUND));
        assertThat(projects.find(deleted.projectId()))
                .as("the system's own read keeps the row")
                .isPresent();
    }

    @Test
    void advanceEditRevisionMovesOnlyFromTheExpectedRevision() {
        ProjectRecord project = create(UUID.randomUUID(), "R");
        Long advanced = tx.execute(s -> projects.advanceEditRevision(project.projectId(), project.ownerUserId(), 0));
        assertThat(advanced).isEqualTo(1L);
        assertThatThrownBy(() ->
                        tx.execute(s -> projects.advanceEditRevision(project.projectId(), project.ownerUserId(), 0)))
                .satisfies(code(ErrorCode.STALE_EDIT_REVISION));
        assertThatThrownBy(
                        () -> tx.execute(s -> projects.advanceEditRevision(project.projectId(), UUID.randomUUID(), 1)))
                .satisfies(code(ErrorCode.PROJECT_NOT_FOUND));
        assertThat(projects.find(project.projectId()).orElseThrow().editRevision())
                .isEqualTo(1L);
    }

    @Test
    void twoTransactionsAdvancingFromTheSameRevisionLetExactlyOneWin() throws Exception {
        ProjectRecord project = create(UUID.randomUUID(), "Both");
        CyclicBarrier start = new CyclicBarrier(2);
        ExecutorService pool = Executors.newFixedThreadPool(2);
        try {
            java.util.List<Future<Long>> results = java.util.stream.Stream.of(1, 2)
                    .map(n -> pool.submit(() -> {
                        start.await(10, TimeUnit.SECONDS);
                        return tx.execute(
                                s -> projects.advanceEditRevision(project.projectId(), project.ownerUserId(), 0));
                    }))
                    .toList();
            int wins = 0;
            int stale = 0;
            for (Future<Long> result : results) {
                try {
                    assertThat(result.get(20, TimeUnit.SECONDS)).isEqualTo(1L);
                    wins++;
                } catch (java.util.concurrent.ExecutionException e) {
                    code(ErrorCode.STALE_EDIT_REVISION).accept(e.getCause());
                    stale++;
                }
            }
            assertThat(wins).isEqualTo(1);
            assertThat(stale).isEqualTo(1);
            assertThat(projects.find(project.projectId()).orElseThrow().editRevision())
                    .isEqualTo(1L);
        } finally {
            pool.shutdownNow();
        }
    }

    @Test
    void anOpenAdvanceKeepsTheRowLockedAgainstASecondTransaction() throws Exception {
        ProjectRecord project = create(UUID.randomUUID(), "Locked");
        CountDownLatch advanced = new CountDownLatch(1);
        CountDownLatch commit = new CountDownLatch(1);
        ExecutorService pool = Executors.newFixedThreadPool(2);
        try {
            Future<Long> first = pool.submit(() -> tx.execute(s -> {
                long revision = projects.advanceEditRevision(project.projectId(), project.ownerUserId(), 0);
                advanced.countDown();
                await(commit);
                return revision;
            }));
            assertThat(advanced.await(10, TimeUnit.SECONDS)).isTrue();
            Future<Long> second = pool.submit(
                    () -> tx.execute(s -> projects.advanceEditRevision(project.projectId(), project.ownerUserId(), 0)));
            assertThatThrownBy(() -> second.get(500, TimeUnit.MILLISECONDS)).isInstanceOf(TimeoutException.class);
            commit.countDown();
            assertThat(first.get(10, TimeUnit.SECONDS)).isEqualTo(1L);
            assertThatThrownBy(() -> second.get(10, TimeUnit.SECONDS))
                    .hasCauseInstanceOf(WebBlockException.class)
                    .satisfies(e -> code(ErrorCode.STALE_EDIT_REVISION).accept(e.getCause()));
        } finally {
            commit.countDown();
            pool.shutdownNow();
        }
    }

    @Test
    void pagingContinuesAcrossRowsCreatedAtTheSameInstant() {
        UUID owner = UUID.randomUUID();
        // the clock does not move, so all three share created_at; the order falls back to the ID, newest ID first
        java.util.List<UUID> ids = new java.util.ArrayList<>(java.util.stream.Stream.of(1, 2, 3)
                .map(n -> create(owner, "t" + n).projectId())
                .toList());
        ids.sort(java.util.Comparator.reverseOrder());
        ProjectPage first = projects.listOwned(owner, 2, null, null, null);
        assertThat(first.items()).extracting(ProjectRecord::projectId).containsExactly(ids.get(0), ids.get(1));
        ProjectPage second = projects.listOwned(owner, 2, first.nextCursor(), null, null);
        assertThat(second.items()).extracting(ProjectRecord::projectId).containsExactly(ids.get(2));
        assertThat(second.nextCursor()).isNull();
    }

    @Test
    void aPageEndingExactlyAtTheLimitHasNoNextCursor() {
        UUID owner = UUID.randomUUID();
        create(owner, "a");
        create(owner, "b");
        ProjectPage page = projects.listOwned(owner, 2, null, null, null);
        assertThat(page.items()).hasSize(2);
        assertThat(page.nextCursor()).isNull();
    }

    @Test
    void theLimitMustBeBetweenOneAndOneHundred() {
        for (int bad : new int[] {0, 101}) {
            assertThatThrownBy(() -> projects.listOwned(UUID.randomUUID(), bad, null, null, null))
                    .satisfies(code(ErrorCode.VALIDATION_FAILED));
        }
        assertThat(projects.listOwned(UUID.randomUUID(), 100, null, null, null).items())
                .isEmpty();
    }

    private static void await(CountDownLatch latch) {
        try {
            latch.await(20, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
