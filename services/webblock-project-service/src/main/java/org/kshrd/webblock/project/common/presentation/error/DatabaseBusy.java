package org.kshrd.webblock.project.common.presentation.error;

import java.sql.SQLTransientConnectionException;
import org.springframework.dao.PessimisticLockingFailureException;
import org.springframework.dao.QueryTimeoutException;

/**
 * The one rule for "the database is busy, try again" (FR-31, TR-24), which REST and gRPC both answer as
 * DEPENDENCY_UNAVAILABLE and log as a warning without a stack trace: a lock wait or statement that ran into the
 * database's timeouts, or no pooled connection free within the pool's timeout (Hikari's
 * {@link SQLTransientConnectionException}, which Spring wraps, for example in {@code CannotCreateTransactionException}).
 */
public final class DatabaseBusy {

    /** Deep enough for Spring's and Hibernate's wrappers; a cause chain that loops ends here. */
    private static final int MAX_CAUSE_DEPTH = 8;

    private DatabaseBusy() {}

    public static boolean is(Throwable failure) {
        if (failure instanceof PessimisticLockingFailureException || failure instanceof QueryTimeoutException) {
            return true;
        }
        Throwable cause = failure;
        for (int depth = 0; cause != null && depth < MAX_CAUSE_DEPTH; depth++) {
            if (cause instanceof SQLTransientConnectionException) {
                return true;
            }
            cause = cause.getCause();
        }
        return false;
    }
}
