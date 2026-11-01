package org.kshrd.webblock.notification.common.presentation;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.slf4j.MDC;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

class TraceContextFilterTest {

    private static final String TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736";
    private static final String TRACEPARENT = "00-" + TRACE_ID + "-00f067aa0ba902b7-01";

    private final TraceContextFilter filter = new TraceContextFilter();

    @AfterEach
    void cleanUp() {
        MDC.clear();
    }

    private MockHttpServletResponse run(MockHttpServletRequest request, AtomicReference<String> traceIdSeen, AtomicReference<String> requestIdSeen)
            throws Exception {
        MockHttpServletResponse response = new MockHttpServletResponse();
        filter.doFilter(request, response, new MockFilterChain() {
            @Override
            public void doFilter(jakarta.servlet.ServletRequest req, jakarta.servlet.ServletResponse res) {
                traceIdSeen.set(MDC.get(TraceContext.TRACE_ID));
                requestIdSeen.set(MDC.get(TraceContext.REQUEST_ID));
            }
        });
        return response;
    }

    @Test
    @DisplayName("continuesTheCallersTraceWhileTheRequestRunsAndAnswersWithTheRequestId")
    void continuesTheCallersTrace() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/notification/v1/users/token");
        request.addHeader("traceparent", TRACEPARENT);
        request.addHeader("x-request-id", "req-abc");
        AtomicReference<String> traceId = new AtomicReference<>();
        AtomicReference<String> requestId = new AtomicReference<>();

        MockHttpServletResponse response = run(request, traceId, requestId);

        assertThat(traceId.get()).isEqualTo(TRACE_ID);
        assertThat(requestId.get()).isEqualTo("req-abc");
        assertThat(response.getHeader("x-request-id")).isEqualTo("req-abc");
        assertThat(MDC.getCopyOfContextMap()).as("context removed after the request").isNullOrEmpty();
    }

    @Test
    @DisplayName("createsARequestIdWhenTheCallerSentNoneAndReturnsIt")
    void createsARequestId() throws Exception {
        AtomicReference<String> traceId = new AtomicReference<>();
        AtomicReference<String> requestId = new AtomicReference<>();

        MockHttpServletResponse response = run(new MockHttpServletRequest("GET", "/actuator/prometheus"), traceId, requestId);

        assertThat(traceId.get()).isNull();
        assertThat(requestId.get()).isNotBlank();
        assertThat(response.getHeader("x-request-id")).isEqualTo(requestId.get());
    }

    @Test
    @DisplayName("neverEchoesAMaliciousRequestIdBackToTheCaller")
    void doesNotEchoMaliciousInput() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/actuator/prometheus");
        request.addHeader("x-request-id", "evil\r\nSet-Cookie: x=1");
        AtomicReference<String> traceId = new AtomicReference<>();
        AtomicReference<String> requestId = new AtomicReference<>();

        MockHttpServletResponse response = run(request, traceId, requestId);

        assertThat(response.getHeader("x-request-id")).doesNotContain("evil").doesNotContain("\n");
    }
}
