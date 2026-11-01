package org.kshrd.webblock.project.common.presentation.error;

import static org.assertj.core.api.Assertions.assertThat;

import jakarta.servlet.RequestDispatcher;
import java.net.URI;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;
import org.kshrd.webblock.project.common.domain.support.RequestIds;
import org.slf4j.MDC;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ProblemDetail;
import org.springframework.mock.web.MockHttpServletRequest;

class ProblemDetailFactoryTest {

    private static MockHttpServletRequest request(String uri) {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", uri);
        request.setAttribute(RequestIds.ATTRIBUTE, "req-1");
        return request;
    }

    @Test
    void buildsEveryMemberFromTheCodeTheDetailsAndTheRequest() {
        List<ErrorDetail> errors = List.of(ErrorDetail.of("pages[1].path", ErrorCode.PAGE_PATH_INVALID));

        ProblemDetail problem =
                ProblemDetailFactory.of(ErrorCode.VALIDATION_FAILED, errors, request("/api/v1/projects/p/pages"));

        assertThat(problem.getType()).isEqualTo(URI.create("urn:webblock:problem:validation-failed"));
        assertThat(problem.getTitle()).isEqualTo("The request is invalid.");
        assertThat(problem.getStatus()).isEqualTo(400);
        assertThat(problem.getInstance()).isEqualTo(URI.create("/api/v1/projects/p/pages"));
        assertThat(problem.getDetail()).isNull();
        assertThat(problem.getProperties())
                .containsEntry("code", "VALIDATION_FAILED")
                .containsEntry("requestId", "req-1")
                .containsEntry("errors", errors)
                .doesNotContainKey("traceId");
    }

    @Test
    void everyCodeHasItsOwnKebabCaseUrn() {
        Set<URI> seen = new HashSet<>();
        for (ErrorCode code : ErrorCode.values()) {
            URI type = ProblemDetailFactory.type(code);
            assertThat(type.toString()).matches("urn:webblock:problem:[a-z0-9]+(-[a-z0-9]+)*");
            assertThat(seen.add(type)).as("unique type for %s", code).isTrue();
        }
        assertThat(ProblemDetailFactory.type(ErrorCode.ADMISSION_RELEASE_PENDING))
                .hasToString("urn:webblock:problem:admission-release-pending");
    }

    @Test
    void theTitleAndTheStatusComeFromTheCatalogue() {
        for (ErrorCode code : ErrorCode.values()) {
            ProblemDetail problem = ProblemDetailFactory.of(code, List.of(), request("/api/x"));
            assertThat(problem.getTitle()).as("title of %s", code).isEqualTo(code.message());
            assertThat(problem.getStatus())
                    .as("status of %s", code)
                    .isEqualTo(code.category().httpStatus());
            assertThat(problem.getProperties()).containsEntry("code", code.name());
        }
    }

    @Test
    void errorsIsAnEmptyListWhenThereAreNone() {
        ProblemDetail problem = ProblemDetailFactory.of(ErrorCode.PROJECT_NOT_FOUND, List.of(), request("/api/x"));
        assertThat(problem.getProperties()).containsEntry("errors", List.of());
    }

    @Test
    void theTraceIdIsAddedOnlyWhenTracingPutOneInTheMdc() {
        MDC.put(RequestIds.TRACE_ID_MDC_KEY, "trace-1");
        try {
            ProblemDetail problem = ProblemDetailFactory.of(ErrorCode.INTERNAL_ERROR, List.of(), request("/api/x"));
            assertThat(problem.getProperties()).containsEntry("traceId", "trace-1");
        } finally {
            MDC.remove(RequestIds.TRACE_ID_MDC_KEY);
        }
    }

    @Test
    void theInstanceIsThePathWithoutTheQuery() {
        MockHttpServletRequest request = request("/api/v1/projects");
        request.setQueryString("note=customer-text");

        ProblemDetail problem = ProblemDetailFactory.of(ErrorCode.VALIDATION_FAILED, List.of(), request);

        assertThat(problem.getInstance()).hasToString("/api/v1/projects");
    }

    @Test
    void onAnErrorDispatchTheInstanceIsTheOriginalPath() {
        MockHttpServletRequest request = request("/error");
        request.setAttribute(RequestDispatcher.ERROR_REQUEST_URI, "/api/%2e%2e/x");

        assertThat(ProblemDetailFactory.instance(request)).hasToString("/api/%2e%2e/x");
    }

    /** Review Focus 1: a container can hand over characters a URI forbids; the error must still be written. */
    @Test
    void aPathThatIsNotAValidUriIsEncodedInsteadOfFailing() {
        MockHttpServletRequest request = request("/error");
        request.setAttribute(RequestDispatcher.ERROR_REQUEST_URI, "/api/a b|c");

        ProblemDetail problem = ProblemDetailFactory.of(ErrorCode.MALFORMED_REQUEST, List.of(), request);

        assertThat(problem.getInstance()).hasToString("/api/a%20b%7Cc");
        assertThat(problem.getStatus()).isEqualTo(400);
    }

    @Test
    void noAcceptHeaderGetsProblemJson() {
        assertThat(ProblemDetailFactory.contentType(request("/api/x"))).hasToString("application/problem+json");
    }

    /** Spec 6.3 content-type rule; the last three rows are Review Focus 2. */
    @ParameterizedTest
    @CsvSource(
            delimiter = '|',
            value = {
                "*/*                                            | application/problem+json",
                "application/*                                  | application/problem+json",
                "application/*+json                             | application/problem+json",
                "application/problem+json                       | application/problem+json",
                "application/json, application/problem+json     | application/problem+json",
                "application/json                               | application/json",
                "text/html, application/json                    | application/json",
                "application/xml                                | application/problem+json",
                "text/html                                      | application/problem+json",
                ";;;                                            | application/problem+json",
                "application/json;q=abc                         | application/problem+json",
                "application/json;Q=abc                         | application/problem+json",
                "application/json;Q=0.5                         | application/json",
                "application/json, text/event-stream            | application/json",
                "text/event-stream                              | application/problem+json",
                "application/problem+json;q=0, application/json | application/json",
                "application/json;q=1.                          | application/json",
                "application/json, application/problem+json;q=0.| application/json",
                "application/json;x=\"a;q=0\"                     | application/json"
            })
    void theContentTypeFollowsTheAcceptHeader(String accept, String expected) {
        MockHttpServletRequest request = request("/api/x");
        request.addHeader(HttpHeaders.ACCEPT, accept);

        assertThat(ProblemDetailFactory.contentType(request)).hasToString(expected);
    }
}
