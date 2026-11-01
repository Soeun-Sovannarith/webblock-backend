package org.kshrd.webblock.notification.infrastructure.client.knock;

import app.knock.api.client.KnockClient;
import app.knock.api.core.JsonValue;
import app.knock.api.core.http.Headers;
import app.knock.api.errors.BadRequestException;
import app.knock.api.errors.InternalServerException;
import app.knock.api.errors.KnockIoException;
import app.knock.api.errors.NotFoundException;
import app.knock.api.errors.PermissionDeniedException;
import app.knock.api.errors.RateLimitException;
import app.knock.api.errors.UnauthorizedException;
import app.knock.api.errors.UnexpectedStatusCodeException;
import app.knock.api.errors.UnprocessableEntityException;
import app.knock.api.models.users.User;
import app.knock.api.models.users.UserUpdateParams;
import app.knock.api.models.workflows.WorkflowTriggerParams;
import app.knock.api.models.workflows.WorkflowTriggerResponse;
import app.knock.api.services.blocking.UserService;
import app.knock.api.services.blocking.WorkflowService;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.kshrd.webblock.notification.common.domain.NotificationDeliveryException;
import org.kshrd.webblock.notification.common.domain.PermanentFailure;
import org.kshrd.webblock.notification.common.domain.PermanentNotificationDeliveryException;
import org.kshrd.webblock.notification.infrastructure.observability.NotificationMetrics;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Map;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * The Knock SDK adapter: what it sends, and how a Knock failure is classified. Permanent = retrying cannot help
 * (dead-letter at once); temporary = worth a few retries. The Knock SDK client is a mock: no test calls the real Knock API.
 */
@ExtendWith(MockitoExtension.class)
class KnockSdkDeliveryClientTest {

    private static final String EMAIL = "owner@example.com";

    @Mock
    private KnockClient knockClient;
    @Mock
    private UserService userService;
    @Mock
    private WorkflowService workflowService;

    private final SimpleMeterRegistry registry = new SimpleMeterRegistry();
    private KnockSdkDeliveryClient client;

    @BeforeEach
    void setUp() {
        lenient().when(knockClient.users()).thenReturn(userService);
        lenient().when(knockClient.workflows()).thenReturn(workflowService);
        client = new KnockSdkDeliveryClient(knockClient, new NotificationMetrics(registry));
    }

    private long knockCalls(String step, String outcome, String statusClass) {
        return registry.get(NotificationMetrics.KNOCK_CALL)
                .tags("step", step, "outcome", outcome, "status_class", statusClass).timer().count();
    }

    private static Headers noHeaders() {
        return Headers.builder().build();
    }

    private static JsonValue noBody() {
        return JsonValue.from(Map.of());
    }

    // ---- what is sent -----------------------------------------------------------------------------------

    @Test
    @DisplayName("identifiesTheRecipientWithIdNameAndEmail")
    void identifySendsTheRecipient() {
        when(userService.update(any(UserUpdateParams.class))).thenReturn(mock(User.class));

        client.identifyRecipient("user-456", "John Doe", EMAIL);

        ArgumentCaptor<UserUpdateParams> sent = ArgumentCaptor.forClass(UserUpdateParams.class);
        verify(userService).update(sent.capture());
        assertThat(sent.getValue().userId()).contains("user-456");
        assertThat(sent.getValue().identifyUserRequest().name()).contains("John Doe");
        assertThat(sent.getValue().identifyUserRequest().email()).contains(EMAIL);
    }

    @Test
    @DisplayName("triggersTheWorkflowForOneRecipientWithTheDataAndTheIdempotencyKey")
    void triggerSendsWorkflowRecipientDataAndKey() {
        WorkflowTriggerResponse response = mock(WorkflowTriggerResponse.class);
        when(response.workflowRunId()).thenReturn("run-789");
        when(workflowService.trigger(any(WorkflowTriggerParams.class))).thenReturn(response);

        String runId = client.triggerWorkflow("deployment-succeeded", "user-456", Map.of("projectId", "p-999"), "msg.v1:key-1:wf:user-456");

        assertThat(runId).isEqualTo("run-789");
        ArgumentCaptor<WorkflowTriggerParams> sent = ArgumentCaptor.forClass(WorkflowTriggerParams.class);
        verify(workflowService).trigger(sent.capture());
        WorkflowTriggerParams params = sent.getValue();
        assertThat(params.key()).contains("deployment-succeeded");
        assertThat(params.recipients()).isNotEmpty();
        assertThat(params.data().orElseThrow()._additionalProperties()).containsKey("projectId");
        // Knock drops a repeat of this key, so a redelivered event is not sent twice
        assertThat(params._additionalHeaders().values("Idempotency-Key")).containsExactly("msg.v1:key-1:wf:user-456");
    }

    // ---- how a failure is classified ---------------------------------------------------------------------

    /** 4xx answers that retrying can never fix, incl. an unknown/missing workflow (404) and wrong credentials (401/403). */
    static Stream<Arguments> permanentFailures() {
        return Stream.of(
                Arguments.of("400 bad request", BadRequestException.builder().headers(noHeaders()).body(noBody()).build(), 400),
                Arguments.of("401 unauthorized", UnauthorizedException.builder().headers(noHeaders()).body(noBody()).build(), 401),
                Arguments.of("403 permission denied", PermissionDeniedException.builder().headers(noHeaders()).body(noBody()).build(), 403),
                Arguments.of("404 workflow missing", NotFoundException.builder().headers(noHeaders()).body(noBody()).build(), 404),
                Arguments.of("422 unprocessable", UnprocessableEntityException.builder().headers(noHeaders()).body(noBody()).build(), 422),
                Arguments.of("418 other 4xx", UnexpectedStatusCodeException.builder().statusCode(418).headers(noHeaders()).body(noBody()).build(), 418));
    }

    /** Rate limit, server errors and network trouble can pass on a later attempt. */
    static Stream<Arguments> temporaryFailures() {
        return Stream.of(
                Arguments.of("429 rate limited", RateLimitException.builder().headers(noHeaders()).body(noBody()).build(), 429),
                Arguments.of("500 server error", InternalServerException.builder().statusCode(500).headers(noHeaders()).body(noBody()).build(), 500),
                Arguments.of("503 unavailable", InternalServerException.builder().statusCode(503).headers(noHeaders()).body(noBody()).build(), 503),
                Arguments.of("502 unexpected 5xx", UnexpectedStatusCodeException.builder().statusCode(502).headers(noHeaders()).body(noBody()).build(), 502),
                Arguments.of("408 request timeout", UnexpectedStatusCodeException.builder().statusCode(408).headers(noHeaders()).body(noBody()).build(), 408),
                Arguments.of("network I/O error", new KnockIoException("connection reset"), null),
                Arguments.of("unknown error", new IllegalStateException("boom"), null));
    }

    @ParameterizedTest(name = "identify: {0}")
    @MethodSource("permanentFailures")
    @DisplayName("treatsARejectedIdentifyCallAsPermanent")
    void identifyPermanent(String name, RuntimeException failure, Integer status) {
        when(userService.update(any(UserUpdateParams.class))).thenThrow(failure);

        assertThatThrownBy(() -> client.identifyRecipient("user-1", "Sokha", EMAIL))
                .isInstanceOf(PermanentNotificationDeliveryException.class)
                .isInstanceOf(PermanentFailure.class)
                .hasMessageContaining("identify")
                .hasMessageNotContaining(EMAIL)
                .hasCause(failure)
                .satisfies(ex -> assertThat(((NotificationDeliveryException) ex).httpStatus()).isEqualTo(status));
    }

    @ParameterizedTest(name = "identify: {0}")
    @MethodSource("temporaryFailures")
    @DisplayName("treatsATemporaryIdentifyFailureAsRetryable")
    void identifyTemporary(String name, RuntimeException failure, Integer status) {
        when(userService.update(any(UserUpdateParams.class))).thenThrow(failure);

        assertThatThrownBy(() -> client.identifyRecipient("user-1", "Sokha", EMAIL))
                .isInstanceOf(NotificationDeliveryException.class)
                .isNotInstanceOf(PermanentFailure.class)
                .hasMessageNotContaining(EMAIL)
                .satisfies(ex -> assertThat(((NotificationDeliveryException) ex).httpStatus()).isEqualTo(status));
    }

    @ParameterizedTest(name = "trigger: {0}")
    @MethodSource("permanentFailures")
    @DisplayName("treatsARejectedWorkflowTriggerAsPermanent")
    void triggerPermanent(String name, RuntimeException failure, Integer status) {
        when(workflowService.trigger(any(WorkflowTriggerParams.class))).thenThrow(failure);

        assertThatThrownBy(() -> client.triggerWorkflow("wf", "user-1", Map.of(), "k1"))
                .isInstanceOf(PermanentNotificationDeliveryException.class)
                .isInstanceOf(PermanentFailure.class)
                .hasMessageContaining("trigger")
                .hasCause(failure)
                .satisfies(ex -> assertThat(((NotificationDeliveryException) ex).httpStatus()).isEqualTo(status));
    }

    @ParameterizedTest(name = "trigger: {0}")
    @MethodSource("temporaryFailures")
    @DisplayName("treatsATemporaryWorkflowTriggerFailureAsRetryable")
    void triggerTemporary(String name, RuntimeException failure, Integer status) {
        when(workflowService.trigger(any(WorkflowTriggerParams.class))).thenThrow(failure);

        assertThatThrownBy(() -> client.triggerWorkflow("wf", "user-1", Map.of(), "k1"))
                .isInstanceOf(NotificationDeliveryException.class)
                .isNotInstanceOf(PermanentFailure.class)
                .satisfies(ex -> assertThat(((NotificationDeliveryException) ex).httpStatus()).isEqualTo(status));
    }

    // ---- what is measured --------------------------------------------------------------------------------

    @Test
    @DisplayName("timesASuccessfulCallPerStep")
    void timesSuccess() {
        when(userService.update(any(UserUpdateParams.class))).thenReturn(mock(User.class));
        when(workflowService.trigger(any(WorkflowTriggerParams.class))).thenReturn(mock(WorkflowTriggerResponse.class));

        client.identifyRecipient("user-1", "Sokha", EMAIL);
        client.triggerWorkflow("wf", "user-1", Map.of(), "k1");

        assertThat(knockCalls("identify", "success", "none")).isEqualTo(1);
        assertThat(knockCalls("trigger", "success", "none")).isEqualTo(1);
    }

    @Test
    @DisplayName("timesAFailedCallWithItsOutcomeAndStatusClass")
    void timesFailures() {
        when(workflowService.trigger(any(WorkflowTriggerParams.class)))
                .thenThrow(UnauthorizedException.builder().headers(noHeaders()).body(noBody()).build())
                .thenThrow(InternalServerException.builder().statusCode(503).headers(noHeaders()).body(noBody()).build())
                .thenThrow(new KnockIoException("connection reset"));

        for (int i = 0; i < 3; i++) {
            assertThatThrownBy(() -> client.triggerWorkflow("wf", "user-1", Map.of(), "k1")).isInstanceOf(NotificationDeliveryException.class);
        }

        assertThat(knockCalls("trigger", "permanent_failure", "4xx")).isEqualTo(1);
        assertThat(knockCalls("trigger", "temporary_failure", "5xx")).isEqualTo(1);
        assertThat(knockCalls("trigger", "temporary_failure", "none")).isEqualTo(1);
    }

    @Test
    @DisplayName("classifiesByStatusCodeSoANew4xxIsNotRetriedForever")
    void classifiesByStatusCode() {
        assertThat(KnockSdkDeliveryClient.isPermanentStatus(400)).isTrue();
        assertThat(KnockSdkDeliveryClient.isPermanentStatus(404)).isTrue();
        assertThat(KnockSdkDeliveryClient.isPermanentStatus(499)).isTrue();
        assertThat(KnockSdkDeliveryClient.isPermanentStatus(408)).isFalse();
        assertThat(KnockSdkDeliveryClient.isPermanentStatus(409)).isFalse();
        assertThat(KnockSdkDeliveryClient.isPermanentStatus(429)).isFalse();
        assertThat(KnockSdkDeliveryClient.isPermanentStatus(500)).isFalse();
        assertThat(KnockSdkDeliveryClient.isPermanentStatus(503)).isFalse();
    }
}
