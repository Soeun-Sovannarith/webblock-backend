package org.kshrd.webblock.project.application.access.validation;

import jakarta.validation.ConstraintValidator;
import jakarta.validation.ConstraintValidatorContext;
import org.kshrd.webblock.project.application.access.model.AccessAction;

/** Null is left to @NotNull (one code per field). */
public final class AccessActionNameValidator implements ConstraintValidator<AccessActionName, String> {

    @Override
    public boolean isValid(String value, ConstraintValidatorContext context) {
        return value == null || AccessAction.fromWireName(value).isPresent();
    }
}
