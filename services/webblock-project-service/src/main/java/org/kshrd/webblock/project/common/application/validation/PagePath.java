package org.kshrd.webblock.project.common.application.validation;

import static java.lang.annotation.ElementType.ANNOTATION_TYPE;
import static java.lang.annotation.ElementType.FIELD;
import static java.lang.annotation.ElementType.METHOD;
import static java.lang.annotation.ElementType.PARAMETER;
import static java.lang.annotation.ElementType.TYPE_USE;
import static java.lang.annotation.RetentionPolicy.RUNTIME;

import jakarta.validation.Constraint;
import jakarta.validation.Payload;
import java.lang.annotation.Documented;
import java.lang.annotation.Retention;
import java.lang.annotation.Target;
import org.kshrd.webblock.project.common.domain.error.ValidationCodes;

/** A public page path: "/" or "/" followed by lowercase segments of letters, digits and hyphens. */
@Documented
@Constraint(validatedBy = PagePathValidator.class)
@Target({FIELD, METHOD, PARAMETER, ANNOTATION_TYPE, TYPE_USE})
@Retention(RUNTIME)
public @interface PagePath {
    String message() default ValidationCodes.PAGE_PATH_INVALID;

    Class<?>[] groups() default {};

    Class<? extends Payload>[] payload() default {};
}
