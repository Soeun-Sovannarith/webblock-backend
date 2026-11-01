package org.kshrd.webblock.project.common.application.validation;

import static org.assertj.core.api.Assertions.assertThat;

import jakarta.validation.ConstraintViolation;
import jakarta.validation.Validation;
import jakarta.validation.ValidatorFactory;
import org.junit.jupiter.api.Test;

class ConstraintMessageTest {

    record Sample(@PagePath String path, @IdempotencyKey String key) {}

    @Test
    void annotationsReportTheirValidationCodeAsMessage() {
        try (ValidatorFactory factory = Validation.buildDefaultValidatorFactory()) {
            var violations = factory.getValidator().validate(new Sample("bad", " k"));
            assertThat(violations)
                    .extracting(ConstraintViolation::getMessage)
                    .containsExactlyInAnyOrder("PAGE_PATH_INVALID", "IDEMPOTENCY_KEY_INVALID");
        }
    }
}
