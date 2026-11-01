package org.kshrd.webblock.notification.common.presentation;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.slf4j.MDC;

import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;

class TraceContextTest {

    private static final String TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736";
    private static final String SPAN_ID = "00f067aa0ba902b7";
    private static final String TRACEPARENT = "00-" + TRACE_ID + "-" + SPAN_ID + "-01";
    private static final String UUID_PATTERN = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";

    @AfterEach
    void cleanUp() {
        MDC.clear();
    }

    @Test
    @DisplayName("continuesTheIncomingTraceAndRequestIdInTheLogContext")
    void continuesTheIncomingTrace() {
        try (TraceContext.Scope ignored = TraceContext.open(TRACEPARENT, "req-123")) {
            assertThat(MDC.get(TraceContext.TRACE_ID)).isEqualTo(TRACE_ID);
            assertThat(MDC.get(TraceContext.SPAN_ID)).isEqualTo(SPAN_ID);
            assertThat(MDC.get(TraceContext.REQUEST_ID)).isEqualTo("req-123");
            assertThat(TraceContext.currentRequestId()).isEqualTo("req-123");
        }
    }

    @ParameterizedTest(name = "[{index}] {0}")
    @ValueSource(strings = {
            "",
            "garbage",
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7",               // flags missing
            "00-4BF92F3577B34DA6A3CE929D0E0E4736-00F067AA0BA902B7-01",            // upper case is not allowed
            "00-00000000000000000000000000000000-00f067aa0ba902b7-01",            // all-zero trace id
            "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01",            // all-zero parent id
            "ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",            // version ff is invalid
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01\nINJECTED",  // line break: log injection
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01-extra"})
    @DisplayName("dropsAMalformedTraceparentInsteadOfInventingOrTrustingIt")
    void dropsMalformedTraceparent(String header) {
        try (TraceContext.Scope ignored = TraceContext.open(header, "req-1")) {
            assertThat(MDC.get(TraceContext.TRACE_ID)).isNull();
            assertThat(MDC.get(TraceContext.SPAN_ID)).isNull();
            assertThat(TraceContext.outgoingHeaders()).doesNotContainKey(TraceContext.TRACEPARENT_HEADER);
        }
    }

    static Stream<String> malformedRequestIds() {
        return Stream.of("", "has space", "line\nbreak", "quote\"inside", "<script>", "x".repeat(129));
    }

    @ParameterizedTest(name = "[{index}]")
    @MethodSource("malformedRequestIds")
    @DisplayName("replacesAMalformedRequestIdWithANewOne")
    void replacesMalformedRequestId(String requestId) {
        try (TraceContext.Scope ignored = TraceContext.open(TRACEPARENT, requestId)) {
            assertThat(MDC.get(TraceContext.REQUEST_ID)).matches(UUID_PATTERN);
        }
    }

    @Test
    @DisplayName("givesEveryLogLineARequestIdEvenWhenTheCallerSentNone")
    void generatesARequestIdWhenMissing() {
        try (TraceContext.Scope ignored = TraceContext.open(null, null)) {
            assertThat(MDC.get(TraceContext.REQUEST_ID)).matches(UUID_PATTERN);
            assertThat(MDC.get(TraceContext.TRACE_ID)).isNull();
        }
    }

    @Test
    @DisplayName("removesTheContextWhenTheScopeClosesSoNothingLeaksToTheNextMessage")
    void clearsOnClose() {
        TraceContext.open(TRACEPARENT, "req-1").close();

        assertThat(MDC.getCopyOfContextMap()).isNullOrEmpty();
        assertThat(TraceContext.currentRequestId()).isNull();
        assertThat(TraceContext.outgoingHeaders()).isEmpty();
    }

    @Test
    @DisplayName("restoresTheOuterContextWhenAnInnerScopeCloses")
    void restoresThePreviousContext() {
        try (TraceContext.Scope outer = TraceContext.open(TRACEPARENT, "outer")) {
            String otherTrace = "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-00";
            TraceContext.open(otherTrace, "inner").close();

            assertThat(MDC.get(TraceContext.TRACE_ID)).isEqualTo(TRACE_ID);
            assertThat(MDC.get(TraceContext.REQUEST_ID)).isEqualTo("outer");
        }
    }

    @Test
    @DisplayName("passesTheIncomingTraceparentAndRequestIdOnUnchangedForCallsToOurOwnServices")
    void outgoingHeaders() {
        try (TraceContext.Scope ignored = TraceContext.open(TRACEPARENT, "req-123")) {
            assertThat(TraceContext.outgoingHeaders())
                    .containsEntry("traceparent", TRACEPARENT)
                    .containsEntry("x-request-id", "req-123")
                    .hasSize(2);
        }
    }
}
