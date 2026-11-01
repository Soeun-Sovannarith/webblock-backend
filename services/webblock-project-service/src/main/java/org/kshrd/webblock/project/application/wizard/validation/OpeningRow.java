package org.kshrd.webblock.project.application.wizard.validation;

import static java.lang.annotation.ElementType.TYPE;
import static java.lang.annotation.RetentionPolicy.RUNTIME;

import jakarta.validation.Constraint;
import jakarta.validation.Payload;
import java.lang.annotation.Documented;
import java.lang.annotation.Retention;
import java.lang.annotation.Target;
import org.kshrd.webblock.project.common.domain.error.ValidationCodes;

/** An open weekday has both times and opens before it closes; the error is reported on the field at fault. */
@Documented
@Constraint(validatedBy = OpeningRowValidator.class)
@Target(TYPE)
@Retention(RUNTIME)
public @interface OpeningRow {
    String message() default ValidationCodes.VALUE_INVALID;

    Class<?>[] groups() default {};

    Class<? extends Payload>[] payload() default {};
}
