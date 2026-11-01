package org.kshrd.webblock.project.application.payment.handler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.awaitility.Awaitility.await;

import java.time.Duration;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.IllegalTransactionStateException;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

/** The per-project release fence (lead ruling J1-R19) on real PostgreSQL 18. */
@ServiceIntegrationTest
class AdmissionReleaseFenceTest {

    @Autowired
    AdmissionReleaseFence releases;

    @Autowired
    PlatformTransactionManager transactions;

    /** The fence's callers pin READ COMMITTED, as the wizard does. */
    TransactionTemplate transaction;

    @Autowired
    JdbcTemplate jdbc;

    @BeforeEach
    void readCommitted() {
        transaction = new TransactionTemplate(transactions);
        transaction.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
    }

    /**
     * Under REPEATABLE READ (a server, role or pool default could set it) the read after the lock wait would use a
     * snapshot taken before the wait and miss what the lock holder committed, so the fence refuses to run.
     */
    @Test
    void theFenceRefusesASnapshotIsolationLevel() {
        for (int level :
                new int[] {TransactionDefinition.ISOLATION_REPEATABLE_READ, TransactionDefinition.ISOLATION_SERIALIZABLE
                }) {
            TransactionTemplate snapshot = new TransactionTemplate(transactions);
            snapshot.setIsolationLevel(level);
            assertThatThrownBy(() -> snapshot.execute(status -> releases.lockAndRead(UUID.randomUUID())))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("READ COMMITTED");
        }
    }

    @Test
    void aProjectWithoutReleasesHasNone() {
        assertThat(releases.state(UUID.randomUUID())).isEqualTo(new AdmissionReleaseFence.State(0, 0));
    }

    @Test
    void aScheduledReleaseIsPendingUntilItsJobCompletes() {
        UUID projectId = UUID.randomUUID();
        transaction.executeWithoutResult(status -> releases.schedule(projectId));
        AdmissionReleaseFence.State scheduled = releases.state(projectId);
        assertThat(scheduled.scheduled()).isEqualTo(1);
        // The stand-in answers NOT_ADMITTED for an unknown project, which completes the job.
        await().atMost(Duration.ofSeconds(30))
                .untilAsserted(
                        () -> assertThat(releases.state(projectId)).isEqualTo(new AdmissionReleaseFence.State(1, 0)));
        assertThat(releases.state(projectId).pending()).isFalse();
        assertThat(new AdmissionReleaseFence.State(1, 1).pending()).isTrue();
    }

    @Test
    void aFailedReleaseJobStaysPending() {
        UUID projectId = UUID.randomUUID();
        transaction.executeWithoutResult(status -> releases.schedule(projectId));
        await().atMost(Duration.ofSeconds(30))
                .untilAsserted(
                        () -> assertThat(releases.state(projectId).pending()).isFalse());
        // A job whose retries are spent: the admission it was meant to release may still be held.
        jdbc.update(
                "UPDATE project_messaging.outbox_record SET status = 'FAILED' WHERE record_key = ?",
                "payment:" + projectId);
        assertThat(releases.state(projectId)).isEqualTo(new AdmissionReleaseFence.State(1, 1));
        assertThat(releases.state(projectId).pending()).isTrue();
    }

    @Test
    void theFenceNeedsTheCallersTransaction() {
        assertThatThrownBy(() -> releases.lockAndRead(UUID.randomUUID()))
                .isInstanceOf(IllegalTransactionStateException.class);
    }

    @Test
    void theFenceSerializesTransactionsOnOneProjectAndReadsAfterTheLock() throws Exception {
        UUID projectId = UUID.randomUUID();
        CountDownLatch holding = new CountDownLatch(1);
        CountDownLatch finish = new CountDownLatch(1);
        ExecutorService pool = Executors.newFixedThreadPool(2);
        try {
            CompletableFuture<Void> first = CompletableFuture.runAsync(
                    () -> transaction.executeWithoutResult(status -> {
                        assertThat(releases.lockAndRead(projectId).scheduled()).isZero();
                        releases.schedule(projectId);
                        holding.countDown();
                        await().atMost(Duration.ofSeconds(10)).until(() -> finish.getCount() == 0);
                    }),
                    pool);
            assertThat(holding.await(10, TimeUnit.SECONDS)).isTrue();
            CompletableFuture<AdmissionReleaseFence.State> second = CompletableFuture.supplyAsync(
                    () -> transaction.execute(status -> releases.lockAndRead(projectId)), pool);
            Thread.sleep(500);
            assertThat(second).as("waits for the first transaction's lock").isNotDone();
            // Another project's fence is not held up.
            AdmissionReleaseFence.State other = transaction.execute(status -> releases.lockAndRead(UUID.randomUUID()));
            assertThat(other.scheduled()).isZero();
            finish.countDown();
            first.get(10, TimeUnit.SECONDS);
            assertThat(second.get(10, TimeUnit.SECONDS).scheduled())
                    .as("reads what the first transaction committed")
                    .isEqualTo(1);
        } finally {
            finish.countDown();
            pool.shutdown();
        }
    }
}
