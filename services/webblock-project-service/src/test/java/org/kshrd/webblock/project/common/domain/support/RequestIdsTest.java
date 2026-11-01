package org.kshrd.webblock.project.common.domain.support;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class RequestIdsTest {

    @Test
    void theNamesAreTheDocumentedOnes() {
        assertThat(RequestIds.HEADER).isEqualTo("X-Request-Id");
        assertThat(RequestIds.MDC_KEY).isEqualTo("requestId");
        assertThat(RequestIds.ATTRIBUTE).isEqualTo("org.kshrd.webblock.project.requestId");
        assertThat(RequestIds.TRACE_ID_MDC_KEY).isEqualTo("traceId");
    }
}
