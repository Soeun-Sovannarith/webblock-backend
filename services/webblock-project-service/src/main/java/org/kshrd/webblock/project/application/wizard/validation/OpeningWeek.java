package org.kshrd.webblock.project.application.wizard.validation;

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

/** Exactly seven opening-hour rows, each weekday once. Null is allowed: the field is optional. */
@Documented
@Constraint(validatedBy = OpeningWeekValidator.class)
@Target({FIELD, PARAMETER, RECORD_COMPONENT})
@Retention(RUNTIME)
public @interface OpeningWeek {
    String message() default ValidationCodes.VALUE_INVALID;

    Class<?>[] groups() default {};

    Class<? extends Payload>[] payload() default {};
}
