package org.kshrd.webblock.project.application.payment.handler;

import io.namastack.outbox.Outbox;
import java.util.UUID;
import org.kshrd.webblock.project.application.messaging.handler.OutboxRecords;
import org.kshrd.webblock.project.application.payment.job.AdmissionReleaseJob;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * Admission releases (decision P9) and the per-project fence that keeps a release away from a project that exists: a
 * retry is handed the very admission a pending release is about to free, because Payment is idempotent on the project
 * ID. A release job is an outbox record keyed {@code payment:<projectId>}; records are never deleted
 * (delete-completed-records is off), so the number scheduled for a project only grows. A purge must keep {@code
 * payment:} records (.claude/docs/runbooks/outbox.md).
 */
@Service
public class AdmissionReleaseFence {

    private static final String LOCK =
            "SELECT current_setting('transaction_isolation'), pg_advisory_xact_lock(hashtextextended(?, 0))";

    /**
     * The release jobs ever scheduled for a project, and how many have not completed. A FAILED job counts as not
     * completed: the admission it was meant to release may still be held.
     */
    public record State(long scheduled, long unfinished) {
        public boolean pending() {
            return unfinished > 0;
        }
    }

    private final Outbox outbox;
    private final OutboxRecords records;
    private final JdbcClient projectDb;

    public AdmissionReleaseFence(Outbox outbox, OutboxRecords records, JdbcClient projectDb) {
        this.outbox = outbox;
        this.records = records;
        this.projectDb = projectDb;
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public void schedule(UUID projectId) {
        outbox.schedule(new AdmissionReleaseJob(projectId), key(projectId));
    }

    /**
     * The project's releases. Without the fence this is a plain read: what a caller sees before it asks Payment for an
     * admission.
     */
    public State state(UUID projectId) {
        OutboxRecords.Count count = records.count(key(projectId));
        return new State(count.records(), count.unfinished());
    }

    /**
     * The fence: takes the project's transaction-scoped advisory lock, then reads its releases. Every transaction that
     * inserts a project or schedules its release runs this first, so the two are serialized per project and each sees
     * what the other committed. The lock is held until the caller's transaction ends; never call Payment under it.
     *
     * <p>Correct only under READ COMMITTED, where each statement takes a new snapshot: under REPEATABLE READ or
     * SERIALIZABLE the snapshot is taken by this first statement, before the lock wait, so the read after it would miss
     * what the lock holder committed, and the two transactions write different rows, so nothing else would notice. Such
     * a transaction is therefore refused.
     */
    @Transactional(propagation = Propagation.MANDATORY)
    public State lockAndRead(UUID projectId) {
        String isolation = projectDb
                .sql(LOCK)
                .param(key(projectId))
                .query((rs, n) -> rs.getString(1))
                .single();
        if (!"read committed".equals(isolation)) {
            throw new IllegalStateException("The admission release fence needs READ COMMITTED, not " + isolation);
        }
        return state(projectId);
    }

    private static String key(UUID projectId) {
        return "payment:" + projectId;
    }
}
