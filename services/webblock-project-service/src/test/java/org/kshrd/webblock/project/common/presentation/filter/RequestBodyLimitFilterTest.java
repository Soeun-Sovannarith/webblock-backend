package org.kshrd.webblock.project.common.presentation.filter;

import static org.assertj.core.api.Assertions.assertThat;

import com.jayway.jsonpath.JsonPath;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.common.presentation.error.ProblemDetailWriter;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

class RequestBodyLimitFilterTest {

    private static final long LIMIT = 100;

    private final RequestBodyLimitFilter filter =
            new RequestBodyLimitFilter(new ProblemDetailWriter(WebTestSupport.problemMapper()), LIMIT);

    private static MockHttpServletRequest post(byte[] body) {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/api/projects");
        request.setContent(body);
        return request;
    }

    @Test
    void rejectsADeclaredLengthOverTheLimitWithoutRunningTheChain() throws Exception {
        MockHttpServletRequest request = post(new byte[(int) LIMIT + 1]);
        request.addHeader("X-Request-Id", "req-9");
        MockHttpServletResponse response = new MockHttpServletResponse();
        AtomicBoolean ran = new AtomicBoolean();

        // RequestIdFilter runs first in production, so it sits in front of the size filter here too.
        new RequestIdFilter()
                .doFilter(request, response, (req, res) -> filter.doFilter(req, res, (r, s) -> ran.set(true)));

        assertThat(ran).isFalse();
        assertThat(response.getStatus()).isEqualTo(413);
        assertThat(response.getContentType()).isEqualTo("application/problem+json");
        String body = response.getContentAsString();
        assertThat(JsonPath.<String>read(body, "$.type")).isEqualTo("urn:webblock:problem:payload-too-large");
        assertThat(JsonPath.<String>read(body, "$.code")).isEqualTo("PAYLOAD_TOO_LARGE");
        assertThat(JsonPath.<Integer>read(body, "$.status")).isEqualTo(413);
        assertThat(JsonPath.<String>read(body, "$.instance")).isEqualTo("/api/projects");
        assertThat(response.getHeader("X-Request-Id")).isEqualTo("req-9");
        assertThat(JsonPath.<String>read(body, "$.requestId")).isEqualTo(response.getHeader("X-Request-Id"));
        assertThat(body).doesNotContain("\"success\"").doesNotContain("\"detail\"");
    }

    @Test
    void letsABodyAtTheLimitThrough() throws Exception {
        MockHttpServletResponse response = new MockHttpServletResponse();
        long[] read = new long[1];

        filter.doFilter(
                post(new byte[(int) LIMIT]),
                response,
                (req, res) -> read[0] = req.getInputStream().readAllBytes().length);

        assertThat(response.getStatus()).isEqualTo(200);
        assertThat(read[0]).isEqualTo(LIMIT);
    }

    @Test
    void answers413WhenAChunkedBodyGrowsPastTheLimitWhileItIsRead() throws Exception {
        MockHttpServletRequest chunked = new MockHttpServletRequest("POST", "/api/projects") {
            @Override
            public long getContentLengthLong() {
                return -1;
            }
        };
        chunked.setContent(new byte[(int) LIMIT + 50]);
        MockHttpServletResponse response = new MockHttpServletResponse();

        filter.doFilter(chunked, response, (req, res) -> req.getInputStream().readAllBytes());

        assertThat(response.getStatus()).isEqualTo(413);
        assertThat(response.getContentType()).isEqualTo("application/problem+json");
        assertThat(JsonPath.<String>read(response.getContentAsString(), "$.code"))
                .isEqualTo("PAYLOAD_TOO_LARGE");
    }
}
