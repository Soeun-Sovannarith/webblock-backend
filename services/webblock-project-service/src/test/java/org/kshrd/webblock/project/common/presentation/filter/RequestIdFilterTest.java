package org.kshrd.webblock.project.common.presentation.filter;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.common.domain.support.RequestIds;
import org.slf4j.MDC;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

class RequestIdFilterTest {

    @Test
    void putsTheIdInTheLoggingContextOnlyDuringTheRequest() throws Exception {
        AtomicReference<String> seen = new AtomicReference<>();
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/x");
        request.addHeader(RequestIds.HEADER, "req-1");

        new RequestIdFilter()
                .doFilter(request, new MockHttpServletResponse(), (req, res) -> seen.set(MDC.get("requestId")));

        assertThat(seen).hasValue("req-1");
        assertThat(MDC.get("requestId")).isNull();
        assertThat(RequestIdFilter.currentRequestId(request)).isEqualTo("req-1");
    }

    @Test
    void clearsTheLoggingContextWhenTheChainThrows() {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/x");

        assertThatThrownBy(() -> new RequestIdFilter().doFilter(request, new MockHttpServletResponse(), (req, res) -> {
                    throw new IllegalStateException("boom");
                }))
                .isInstanceOf(IllegalStateException.class)
                .hasMessage("boom");

        assertThat(MDC.get("requestId")).isNull();
    }

    @Test
    void theErrorDispatchKeepsTheIdOfTheRequestInTheLoggingContext() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/x");
        MockHttpServletResponse first = new MockHttpServletResponse();
        AtomicReference<String> firstId = new AtomicReference<>();
        new RequestIdFilter().doFilter(request, first, (req, res) -> firstId.set(MDC.get("requestId")));

        // The container re-dispatches the same request object to /error, after the first pass has finished.
        request.setDispatcherType(jakarta.servlet.DispatcherType.ERROR);
        MockHttpServletResponse second = new MockHttpServletResponse();
        AtomicReference<String> errorId = new AtomicReference<>();
        new RequestIdFilter().doFilter(request, second, (req, res) -> errorId.set(MDC.get("requestId")));

        assertThat(errorId.get()).isNotNull().isEqualTo(firstId.get());
        assertThat(second.getHeader(RequestIds.HEADER)).isEqualTo(firstId.get());
        assertThat(MDC.get("requestId")).isNull();
    }
}
