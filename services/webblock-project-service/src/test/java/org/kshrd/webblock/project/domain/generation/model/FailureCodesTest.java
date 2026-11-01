package org.kshrd.webblock.project.domain.generation.model;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;

class FailureCodesTest {

    @ParameterizedTest
    @CsvSource({
        "TRIAL_GENERATION_LIMIT_REACHED, trial_generation_limit_reached",
        "TRIAL_ENDED, trial_ended",
        "NO_ACTIVE_PLAN, no_active_plan",
        "CLIENT_SUSPENDED, client_suspended",
        "PROJECT_NOT_ADMITTED, project_not_admitted",
        "PLAN_PAUSED, plan_paused"
    })
    void aRefusalIsStoredAsItsLowerCase(String reason, String code) {
        assertThat(FailureCodes.forRefusal(reason)).isEqualTo(code);
    }

    @ParameterizedTest
    @ValueSource(strings = {"", "PLAN PAUSED", "PLAN-PAUSED", "PLAN_2", "Ünknown"})
    void aReasonWhoseLowerCaseDoesNotMatchIsAnAdmissionError(String reason) {
        assertThat(FailureCodes.forRefusal(reason)).isEqualTo("admission_error");
    }

    @Test
    void aNullOrSixtyFiveCharacterReasonIsAnAdmissionError() {
        assertThat(FailureCodes.forRefusal(null)).isEqualTo(FailureCodes.ADMISSION_ERROR);
        assertThat(FailureCodes.forRefusal("A".repeat(65))).isEqualTo("admission_error");
        assertThat(FailureCodes.forRefusal("A".repeat(64))).isEqualTo("a".repeat(64));
    }

    @Test
    void theFixedCodesHaveTheirSpecValues() {
        assertThat(FailureCodes.PROJECT_UNAVAILABLE).isEqualTo("project_unavailable");
        assertThat(FailureCodes.ADMISSION_UNAVAILABLE).isEqualTo("admission_unavailable");
        assertThat(FailureCodes.ADMISSION_ERROR).isEqualTo("admission_error");
    }
}
