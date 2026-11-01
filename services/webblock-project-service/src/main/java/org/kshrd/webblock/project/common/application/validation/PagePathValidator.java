package org.kshrd.webblock.project.common.application.validation;

import jakarta.validation.ConstraintValidator;
import jakarta.validation.ConstraintValidatorContext;
import org.kshrd.webblock.project.common.domain.support.PagePaths;

/** Bean Validation for {@link PagePath}: the rule of {@link PagePaths}; null is left to {@code @NotNull}. */
public final class PagePathValidator implements ConstraintValidator<PagePath, String> {

    @Override
    public boolean isValid(String value, ConstraintValidatorContext context) {
        return value == null || PagePaths.isValid(value);
    }
}
