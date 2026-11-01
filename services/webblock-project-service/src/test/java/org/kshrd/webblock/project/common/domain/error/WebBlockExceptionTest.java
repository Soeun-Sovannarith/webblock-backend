package org.kshrd.webblock.project.common.domain.error;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatNullPointerException;

import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

class WebBlockExceptionTest {

    @Test
    void carriesItsCodeAsMessageAndCopiesDetails() {
        List<ErrorDetail> details = new ArrayList<>(List.of(ErrorDetail.of("path", ErrorCode.PAGE_PATH_INVALID)));
        WebBlockException ex = new WebBlockException(ErrorCode.VALIDATION_FAILED, details);
        details.clear();

        assertThat(ex.getMessage()).isEqualTo("VALIDATION_FAILED");
        assertThat(ex.errorCode()).isEqualTo(ErrorCode.VALIDATION_FAILED);
        assertThat(ex.details())
                .containsExactly(new ErrorDetail("path", "PAGE_PATH_INVALID", ErrorCode.PAGE_PATH_INVALID.message()));
    }

    @Test
    void keepsTheCause() {
        IllegalStateException cause = new IllegalStateException("x");
        assertThat(new WebBlockException(ErrorCode.INTERNAL_ERROR, cause).getCause())
                .isSameAs(cause);
    }

    @Test
    void requiresACode() {
        assertThatNullPointerException().isThrownBy(() -> new WebBlockException(null));
    }
}
