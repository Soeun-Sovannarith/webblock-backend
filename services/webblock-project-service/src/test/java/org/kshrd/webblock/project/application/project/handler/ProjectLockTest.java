package org.kshrd.webblock.project.application.project.handler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.project.model.NewProject;
import org.kshrd.webblock.project.application.project.model.ProjectRecord;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import org.kshrd.webblock.project.infrastructure.config.project.ProjectConfigImport;
import org.kshrd.webblock.project.support.JpaSliceTest;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.IllegalTransactionStateException;
import org.springframework.transaction.support.TransactionTemplate;

@JpaSliceTest
@Import({ProjectService.class, ProjectConfigImport.class})
class ProjectLockTest {

    @Autowired
    ProjectService projects;

    @Autowired
    TransactionTemplate tx;

    @Autowired
    JdbcTemplate jdbc;

    private ProjectRecord create(UUID owner) {
        UUID id = Uuids.v7();
        return tx.execute(s -> projects.create(
                new NewProject(id, owner, "Locked", TestSubdomains.of(id), UUID.randomUUID(), "0.1.0")));
    }

    @Test
    void anotherOwnerOrAnUnknownProjectIsNotFound() {
        ProjectRecord p = create(UUID.randomUUID());
        assertThatThrownBy(() -> tx.executeWithoutResult(s -> projects.lockOwned(p.projectId(), UUID.randomUUID())))
                .isInstanceOfSatisfying(
                        WebBlockException.class, e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.PROJECT_NOT_FOUND));
        assertThatThrownBy(() -> tx.executeWithoutResult(s -> projects.lockOwned(Uuids.v7(), p.ownerUserId())))
                .isInstanceOfSatisfying(
                        WebBlockException.class, e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.PROJECT_NOT_FOUND));
    }

    @Test
    void aSuspendedProjectIsReturnedForTheCallerToDecide() {
        ProjectRecord p = create(UUID.randomUUID());
        jdbc.update("UPDATE project.projects SET status = 'suspended' WHERE project_id = ?", p.projectId());
        ProjectRecord locked = tx.execute(s -> projects.lockOwned(p.projectId(), p.ownerUserId()));
        assertThat(locked.projectId()).isEqualTo(p.projectId());
        assertThat(locked.readOnly()).isTrue();
        assertThat(locked.platformHostname()).isEqualTo(p.platformHostname());
    }

    @Test
    @org.junit.jupiter.api.DisplayName("FR-38.3: a deleted project cannot be locked: PROJECT_NOT_FOUND")
    void fr38_3_aDeletedProjectIsNotFound() {
        ProjectRecord p = create(UUID.randomUUID());
        jdbc.update("UPDATE project.projects SET deleted_at = now() WHERE project_id = ?", p.projectId());
        assertThatThrownBy(() -> tx.executeWithoutResult(s -> projects.lockOwned(p.projectId(), p.ownerUserId())))
                .isInstanceOfSatisfying(
                        WebBlockException.class, e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.PROJECT_NOT_FOUND));
    }

    @Test
    void needsATransaction() {
        assertThatThrownBy(() -> projects.lockOwned(Uuids.v7(), UUID.randomUUID()))
                .isInstanceOf(IllegalTransactionStateException.class);
    }

    @Test
    void aSecondLockWaitsForTheFirstTransaction() throws Exception {
        ProjectRecord p = create(UUID.randomUUID());
        CountDownLatch locked = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        AtomicBoolean firstCommitted = new AtomicBoolean();
        AtomicBoolean secondSawCommit = new AtomicBoolean();
        ExecutorService pool = Executors.newFixedThreadPool(2);
        try {
            Future<?> first = pool.submit(() -> tx.executeWithoutResult(s -> {
                projects.lockOwned(p.projectId(), p.ownerUserId());
                locked.countDown();
                await(release);
                firstCommitted.set(true);
            }));
            assertThat(locked.await(30, TimeUnit.SECONDS)).isTrue();
            Future<?> second = pool.submit(() -> tx.executeWithoutResult(s -> {
                projects.lockOwned(p.projectId(), p.ownerUserId());
                secondSawCommit.set(firstCommitted.get());
            }));
            assertThatThrownBy(() -> second.get(1, TimeUnit.SECONDS)).isInstanceOf(TimeoutException.class);
            release.countDown();
            first.get(30, TimeUnit.SECONDS);
            second.get(30, TimeUnit.SECONDS);
            assertThat(secondSawCommit).isTrue();
        } finally {
            release.countDown();
            pool.shutdownNow();
        }
    }

    @Test
    void aPageSaveWaitsForTheLock() throws Exception {
        ProjectRecord p = create(UUID.randomUUID());
        CountDownLatch locked = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        ExecutorService pool = Executors.newFixedThreadPool(2);
        try {
            Future<?> first = pool.submit(() -> tx.executeWithoutResult(s -> {
                projects.lockOwned(p.projectId(), p.ownerUserId());
                locked.countDown();
                await(release);
            }));
            assertThat(locked.await(30, TimeUnit.SECONDS)).isTrue();
            Future<Long> save =
                    pool.submit(() -> tx.execute(s -> projects.advanceEditRevision(p.projectId(), p.ownerUserId(), 0)));
            assertThatThrownBy(() -> save.get(1, TimeUnit.SECONDS)).isInstanceOf(TimeoutException.class);
            release.countDown();
            first.get(30, TimeUnit.SECONDS);
            assertThat(save.get(30, TimeUnit.SECONDS)).isEqualTo(1);
        } finally {
            release.countDown();
            pool.shutdownNow();
        }
    }

    private static void await(CountDownLatch latch) {
        try {
            if (!latch.await(30, TimeUnit.SECONDS)) {
                throw new IllegalStateException("never released");
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException(e);
        }
    }
}
