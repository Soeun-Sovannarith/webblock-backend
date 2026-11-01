package org.kshrd.webblock.project.common.presentation.error;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.support.RequestIds;
import org.kshrd.webblock.project.common.presentation.filter.WebTestSupport;
import org.springframework.http.HttpHeaders;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

class ProblemDetailWriterTest {

    private final JsonMapper mapper = WebTestSupport.problemMapper();
    private final ProblemDetailWriter writer = new ProblemDetailWriter(mapper);

    private static MockHttpServletRequest request() {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/api/v1/projects");
        request.setAttribute(RequestIds.ATTRIBUTE, "req-7");
        return request;
    }

    @Test
    void writesTheStatusTheContentTypeAndEveryMember() throws Exception {
        MockHttpServletResponse response = new MockHttpServletResponse();

        writer.write(request(), response, ErrorCode.PAYLOAD_TOO_LARGE);

        assertThat(response.getStatus()).isEqualTo(413);
        assertThat(response.getContentType()).isEqualTo("application/problem+json");
        JsonNode body = mapper.readTree(response.getContentAsByteArray());
        assertThat(body.get("type").asString()).isEqualTo("urn:webblock:problem:payload-too-large");
        assertThat(body.get("title").asString()).isEqualTo(ErrorCode.PAYLOAD_TOO_LARGE.message());
        assertThat(body.get("status").asInt()).isEqualTo(413);
        assertThat(body.get("instance").asString()).isEqualTo("/api/v1/projects");
        assertThat(body.get("code").asString()).isEqualTo("PAYLOAD_TOO_LARGE");
        assertThat(body.get("requestId").asString()).isEqualTo("req-7");
        assertThat(body.get("errors").isArray()).isTrue();
        assertThat(body.get("errors").size()).isZero();
        assertThat(body.has("detail")).isFalse();
        assertThat(body.has("traceId")).isFalse();
        assertThat(body.has("properties")).isFalse();
        assertThat(body.has("success")).isFalse();
    }

    @Test
    void aJsonOnlyClientGetsTheSameBodyAsJson() throws Exception {
        MockHttpServletRequest request = request();
        request.addHeader(HttpHeaders.ACCEPT, "application/json");
        MockHttpServletResponse response = new MockHttpServletResponse();

        writer.write(request, response, ErrorCode.ACCESS_DENIED);

        assertThat(response.getContentType()).isEqualTo("application/json");
        JsonNode body = mapper.readTree(response.getContentAsByteArray());
        assertThat(body.get("type").asString()).isEqualTo("urn:webblock:problem:access-denied");
        assertThat(body.get("status").asInt()).isEqualTo(403);
    }

    /** Review Focus 5: every 401 carries a Bearer challenge; a caller's more specific challenge is kept. */
    @Test
    void a401CarriesTheBearerChallengeUnlessTheCallerSetAMoreSpecificOne() throws Exception {
        MockHttpServletResponse plain = new MockHttpServletResponse();
        writer.write(request(), plain, ErrorCode.UNAUTHENTICATED);
        assertThat(plain.getHeader(HttpHeaders.WWW_AUTHENTICATE)).isEqualTo("Bearer");

        MockHttpServletResponse refused = new MockHttpServletResponse();
        refused.setHeader(HttpHeaders.WWW_AUTHENTICATE, "Bearer error=\"invalid_token\"");
        writer.write(request(), refused, ErrorCode.UNAUTHENTICATED);
        assertThat(refused.getHeaders(HttpHeaders.WWW_AUTHENTICATE)).containsExactly("Bearer error=\"invalid_token\"");

        MockHttpServletResponse forbidden = new MockHttpServletResponse();
        writer.write(request(), forbidden, ErrorCode.ACCESS_DENIED);
        assertThat(forbidden.getHeader(HttpHeaders.WWW_AUTHENTICATE)).isNull();
    }

    @Test
    void bufferedOutputOfAHalfRunHandlerNeverPrecedesTheJson() throws Exception {
        MockHttpServletResponse response = new MockHttpServletResponse();
        response.getOutputStream().write("stale-partial-output".getBytes(StandardCharsets.UTF_8));

        writer.write(request(), response, ErrorCode.ACCESS_DENIED);

        assertThat(response.getContentAsString())
                .doesNotContain("stale-partial-output")
                .startsWith("{");
        assertThat(mapper.readTree(response.getContentAsByteArray()).get("code").asString())
                .isEqualTo("ACCESS_DENIED");
    }
}
