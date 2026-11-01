package org.kshrd.webblock.project.architecture;

import static org.assertj.core.api.Assertions.assertThat;

import com.tngtech.archunit.core.importer.ClassFileImporter;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.architecture.fixture.EnvelopeErrorBuilder;
import org.kshrd.webblock.project.common.presentation.envelope.ApiResponse;
import org.kshrd.webblock.project.common.presentation.envelope.ResponseEnvelopeAdvice;
import org.kshrd.webblock.project.common.presentation.error.GlobalExceptionHandler;

class EnvelopeRuleTest {

    @Test
    void theRuleRefusesAnEnvelopeBuiltOutsideTheAdvice() {
        var classes = new ClassFileImporter().importClasses(EnvelopeErrorBuilder.class);
        assertThat(ArchitectureRulesTest.onlyTheEnvelopeAdviceUsesApiResponse
                        .evaluate(classes)
                        .hasViolation())
                .isTrue();
    }

    @Test
    void theRuleAcceptsTheAdviceAndTheExceptionHandler() {
        var classes = new ClassFileImporter()
                .importClasses(ApiResponse.class, ResponseEnvelopeAdvice.class, GlobalExceptionHandler.class);
        assertThat(ArchitectureRulesTest.onlyTheEnvelopeAdviceUsesApiResponse
                        .evaluate(classes)
                        .hasViolation())
                .isFalse();
    }
}
