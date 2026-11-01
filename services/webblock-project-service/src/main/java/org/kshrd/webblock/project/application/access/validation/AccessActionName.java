package org.kshrd.webblock.project.application.access.validation;

import static java.lang.annotation.ElementType.FIELD;
import static java.lang.annotation.ElementType.PARAMETER;
import static java.lang.annotation.ElementType.RECORD_COMPONENT;
import static java.lang.annotation.RetentionPolicy.RUNTIME;

import jakarta.validation.Constraint;
import jakarta.validation.Payload;
import java.lang.annotation.Documented;
import java.lang.annotation.Retention;
import java.lang.annotation.Target;
import org.kshrd.webblock.project.common.domain.error.ValidationCodes;

/**
 * The exact wire name of an accepted action (FR-32): no other case, no spaces around it. Anything else is
 * VALUE_INVALID, over REST and gRPC alike (FR-30.6, FR-31.5).
 */
@Documented
@Constraint(validatedBy = AccessActionNameValidator.class)
@Target({FIELD, PARAMETER, RECORD_COMPONENT})
@Retention(RUNTIME)
public @interface AccessActionName {
    String message() default ValidationCodes.VALUE_INVALID;

    Class<?>[] groups() default {};

    Class<? extends Payload>[] payload() default {};
}
