package org.kshrd.webblock.project.common.domain.error;

import java.util.Map;

/**
 * A feature's database constraint names and the codes their violations report. A feature declares one as a bean;
 * {@code GlobalExceptionHandler} collects them all.
 */
@FunctionalInterface
public interface ConstraintErrorRegistration {
    Map<String, ErrorCode> constraints();
}
