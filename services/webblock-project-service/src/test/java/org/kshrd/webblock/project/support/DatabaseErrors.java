package org.kshrd.webblock.project.support;

import java.util.Optional;
import org.hibernate.exception.ConstraintViolationException;

/** The violated database constraint, as PostgreSQL names it, anywhere in a failure's cause chain. */
public final class DatabaseErrors {

    private DatabaseErrors() {}

    public static Optional<String> constraintName(Throwable failure) {
        for (Throwable t = failure; t != null; t = t.getCause()) {
            if (t instanceof ConstraintViolationException violation && violation.getConstraintName() != null) {
                return Optional.of(violation.getConstraintName());
            }
        }
        return Optional.empty();
    }
}
