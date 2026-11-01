package org.kshrd.webblock.project.common.presentation.error;

import static org.assertj.core.api.Assertions.assertThat;

import jakarta.servlet.RequestDispatcher;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.common.presentation.filter.WebTestSupport;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

@Tag("unit")
class ProblemErrorControllerTest {

    private final JsonMapper mapper = WebTestSupport.problemMapper();
    private final ProblemErrorController controller = new ProblemErrorController(new ProblemDetailWriter(mapper));

    private JsonNode dispatch(Integer status, MockHttpServletResponse response) throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/error");
        if (status != null) {
            request.setAttribute(RequestDispatcher.ERROR_STATUS_CODE, status);
        }
        request.setAttribute(RequestDispatcher.ERROR_REQUEST_URI, "/api/original");
        request.setAttribute(RequestDispatcher.ERROR_EXCEPTION, new IllegalStateException("secret detail"));
        controller.error(request, response);
        return mapper.readTree(response.getContentAsByteArray());
    }

    @Test
    void a500BecomesInternalErrorWithoutLeakingTheMessage() throws Exception {
        MockHttpServletResponse response = new MockHttpServletResponse();
        JsonNode body = dispatch(500, response);
        assertThat(response.getStatus()).isEqualTo(500);
        assertThat(response.getContentType()).isEqualTo("application/problem+json");
        assertThat(body.get("type").asString()).isEqualTo("urn:webblock:problem:internal-error");
        assertThat(body.get("code").asString()).isEqualTo("INTERNAL_ERROR");
        assertThat(body.get("status").asInt()).isEqualTo(500);
        assertThat(body.get("instance").asString()).isEqualTo("/api/original");
        assertThat(body.has("detail")).isFalse();
        assertThat(response.getContentAsString())
                .doesNotContain("secret detail")
                .doesNotContain("IllegalStateException");
    }

    @Test
    void a404BecomesRouteNotFoundWithTheOriginalPath() throws Exception {
        MockHttpServletResponse response = new MockHttpServletResponse();
        JsonNode body = dispatch(404, response);
        assertThat(response.getStatus()).isEqualTo(404);
        assertThat(body.get("code").asString()).isEqualTo("ROUTE_NOT_FOUND");
        assertThat(body.get("instance").asString()).isEqualTo("/api/original");
        assertThat(body.has("success")).isFalse();
    }

    @Test
    void anUnknown4xxIsAMalformedRequestAndAMissingStatusIsInternal() throws Exception {
        assertThat(dispatch(400, new MockHttpServletResponse()).get("code").asString())
                .isEqualTo("MALFORMED_REQUEST");
        assertThat(dispatch(null, new MockHttpServletResponse()).get("code").asString())
                .isEqualTo("INTERNAL_ERROR");
    }

    /** Review Focus 5. */
    @Test
    void a401FromTheContainerCarriesTheBearerChallenge() throws Exception {
        MockHttpServletResponse response = new MockHttpServletResponse();
        JsonNode body = dispatch(401, response);
        assertThat(response.getStatus()).isEqualTo(401);
        assertThat(response.getHeader("WWW-Authenticate")).isEqualTo("Bearer");
        assertThat(body.get("code").asString()).isEqualTo("UNAUTHENTICATED");
    }
}
