package org.kshrd.webblock.project.common.presentation.error;

import java.sql.SQLException;
import java.sql.SQLTransientConnectionException;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.springframework.dao.QueryTimeoutException;
import org.springframework.transaction.CannotCreateTransactionException;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.core.exc.StreamConstraintsException;

@RestController
class DependencyProbeController {

    @GetMapping("/api/test-dependency")
    String fail() {
        throw new WebBlockException(ErrorCode.PAYMENT_UNAVAILABLE);
    }

    @GetMapping("/api/test-dependency/release-pending")
    String releasePending() {
        throw new WebBlockException(ErrorCode.ADMISSION_RELEASE_PENDING);
    }

    @GetMapping("/api/test-dependency/other-conflict")
    String otherConflict() {
        throw new WebBlockException(ErrorCode.PROJECT_ID_CONFLICT);
    }

    /** Thrown by service code (not by a request body read, which Spring wraps in HttpMessageNotReadableException). */
    @GetMapping("/api/test-dependency/stream-constraints")
    String streamConstraints() {
        throw new StreamConstraintsException("Document nesting depth (1001) exceeds the maximum allowed (1000)");
    }

    @GetMapping("/api/test-dependency/statement-timeout")
    String statementTimeout() {
        throw new QueryTimeoutException("canceling statement due to statement timeout: SELECT secret");
    }

    /** What JPA throws when the pool has no free connection within its timeout. */
    @GetMapping("/api/test-dependency/pool-timeout")
    String poolTimeout() {
        throw new CannotCreateTransactionException(
                "Could not open JPA EntityManager for transaction",
                new SQLTransientConnectionException(
                        "HikariPool-1 - Connection is not available, request timed out after 30000ms."));
    }

    /** The same wrapper with any other cause is not a busy database. */
    @GetMapping("/api/test-dependency/transaction-failure")
    String transactionFailure() {
        throw new CannotCreateTransactionException("Could not open JPA EntityManager", new SQLException("secret"));
    }
}
