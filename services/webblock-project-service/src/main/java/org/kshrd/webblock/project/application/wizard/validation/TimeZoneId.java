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

/** An IANA time zone region ID known to the JDK, such as Asia/Phnom_Penh or UTC, of at most 100 characters. */
@Documented
@Constraint(validatedBy = TimeZoneIdValidator.class)
@Target({FIELD, PARAMETER, RECORD_COMPONENT})
@Retention(RUNTIME)
public @interface TimeZoneId {
    String message() default ValidationCodes.VALUE_INVALID;

    Class<?>[] groups() default {};

    Class<? extends Payload>[] payload() default {};
}
