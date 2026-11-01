package org.kshrd.webblock.project.common.presentation.error;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatIllegalStateException;

import java.sql.SQLException;
import java.util.List;
import java.util.Map;
import org.hibernate.exception.ConstraintViolationException;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.common.domain.error.ConstraintErrorRegistration;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.mock.web.MockHttpServletRequest;

/** How GlobalExceptionHandler turns a violated database constraint into the code a feature registered for it. */
class ConstraintErrorMappingTest {

    private static DataIntegrityViolationException violation(String constraint) {
        return new DataIntegrityViolationException(
                "x", new ConstraintViolationException("x", new SQLException("x"), constraint));
    }

    private static String codeOf(GlobalExceptionHandler handler, DataIntegrityViolationException failure) {
        return (String) handler.handleIntegrity(failure, new MockHttpServletRequest("POST", "/api/x"))
                .getBody()
                .getProperties()
                .get("code");
    }

    @Test
    void mapsARegisteredConstraintFoundAnywhereInTheCauseChain() {
        ConstraintErrorRegistration probe = () -> Map.of("probe_name_key", ErrorCode.CONCURRENT_UPDATE);
        GlobalExceptionHandler handler = new GlobalExceptionHandler(List.of(probe), 5);
        assertThat(codeOf(handler, violation("probe_name_key"))).isEqualTo("CONCURRENT_UPDATE");
    }

    @Test
    void unknownOrMissingConstraintsMapToNothing() {
        GlobalExceptionHandler handler = new GlobalExceptionHandler(List.of(), 5);
        assertThat(codeOf(handler, violation("other_key"))).isEqualTo("INTERNAL_ERROR");
        assertThat(codeOf(handler, new DataIntegrityViolationException("no cause")))
                .isEqualTo("INTERNAL_ERROR");
    }

    @Test
    void refusesTheSameConstraintMappedToTwoCodes() {
        ConstraintErrorRegistration first = () -> Map.of("k", ErrorCode.CONCURRENT_UPDATE);
        ConstraintErrorRegistration second = () -> Map.of("k", ErrorCode.VALUE_INVALID);
        assertThatIllegalStateException()
                .isThrownBy(() -> new GlobalExceptionHandler(List.of(first, second), 5))
                .withMessageContaining("k");
    }
}
