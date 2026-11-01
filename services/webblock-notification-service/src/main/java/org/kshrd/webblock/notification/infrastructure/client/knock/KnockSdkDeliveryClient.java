package org.kshrd.webblock.notification.infrastructure.client.knock;

import app.knock.api.client.KnockClient;
import app.knock.api.core.JsonValue;
import app.knock.api.errors.KnockServiceException;
import app.knock.api.models.users.IdentifyUserRequest;
import app.knock.api.models.users.UserUpdateParams;
import app.knock.api.models.workflows.WorkflowTriggerParams;
import app.knock.api.models.workflows.WorkflowTriggerResponse;
import io.micrometer.core.instrument.Timer;
import org.kshrd.webblock.notification.common.domain.NotificationDeliveryException;
import org.kshrd.webblock.notification.common.domain.PermanentFailure;
import org.kshrd.webblock.notification.common.domain.PermanentNotificationDeliveryException;
import org.kshrd.webblock.notification.infrastructure.client.KnockDeliveryClient;
import org.kshrd.webblock.notification.infrastructure.observability.NotificationMetrics;
import org.springframework.stereotype.Component;

import java.util.Map;

/**
 * Knock through the official Java SDK:
 * - identify: {@code knockClient.users().update(...)} (Knock's identify / upsert API)
 * - trigger: {@code knockClient.workflows().trigger(...)} with the Idempotency-Key header
 * It also decides which failures are permanent and which are worth a retry.
 */
@Component
public class KnockSdkDeliveryClient implements KnockDeliveryClient {

    private final KnockClient knockClient;
    private final NotificationMetrics metrics;

    public KnockSdkDeliveryClient(KnockClient knockClient, NotificationMetrics metrics) {
        this.knockClient = knockClient;
        this.metrics = metrics;
    }

    @Override
    public void identifyRecipient(String recipientId, String name, String email) {
        IdentifyUserRequest identifyRequest = IdentifyUserRequest.builder()
                .name(name)
                .email(email)
                .build();

        UserUpdateParams updateParams = UserUpdateParams.builder()
                .userId(recipientId)
                .identifyUserRequest(identifyRequest)
                .build();

        Timer.Sample sample = metrics.startKnockCall();
        try {
            knockClient.users().update(updateParams);
            metrics.stopKnockCall(sample, "identify", NotificationMetrics.SUCCESS, null);
        } catch (Exception ex) {
            NotificationDeliveryException failure = failure("Failed to identify user in Knock: " + recipientId, ex);
            metrics.stopKnockCall(sample, "identify", outcomeOf(failure), failure.httpStatus());
            throw failure;
        }
    }

    @Override
    public String triggerWorkflow(String workflowKey, String recipientId, Map<String, Object> data, String idempotencyKey) {
        WorkflowTriggerParams.Data.Builder dataBuilder = WorkflowTriggerParams.Data.builder();
        if (data != null) {
            data.forEach((key, value) -> dataBuilder.putAdditionalProperty(key, JsonValue.from(value)));
        }

        WorkflowTriggerParams triggerParams = WorkflowTriggerParams.builder()
                .key(workflowKey)
                .addRecipient(recipientId)
                .data(dataBuilder.build())
                .putAdditionalHeader("Idempotency-Key", idempotencyKey)
                .build();

        Timer.Sample sample = metrics.startKnockCall();
        try {
            WorkflowTriggerResponse response = knockClient.workflows().trigger(triggerParams);
            metrics.stopKnockCall(sample, "trigger", NotificationMetrics.SUCCESS, null);
            return response.workflowRunId();
        } catch (Exception ex) {
            NotificationDeliveryException failure = failure("Failed to trigger Knock workflow: " + workflowKey, ex);
            metrics.stopKnockCall(sample, "trigger", outcomeOf(failure), failure.httpStatus());
            throw failure;
        }
    }

    private static String outcomeOf(NotificationDeliveryException failure) {
        return failure instanceof PermanentFailure ? NotificationMetrics.PERMANENT_FAILURE : NotificationMetrics.TEMPORARY_FAILURE;
    }

    /**
     * Permanent (4xx except 408/409/425/429): the request itself is wrong or not allowed, so a retry cannot help.
     * This covers a bad payload, wrong or missing credentials (401/403), a missing workflow (404) and 422.
     * Everything else is temporary: 429, 5xx, network errors, timeouts and unknown errors get the bounded listener retry.
     * 409 is treated as temporary because it can mean "same Idempotency-Key still in progress" (not verified with Knock).
     */
    static boolean isPermanentStatus(int status) {
        return status >= 400 && status < 500 && status != 408 && status != 409 && status != 425 && status != 429;
    }

    private static NotificationDeliveryException failure(String message, Exception ex) {
        if (ex instanceof KnockServiceException serviceError) {
            int status = serviceError.statusCode();
            return isPermanentStatus(status)
                    ? new PermanentNotificationDeliveryException(message, ex, status)
                    : new NotificationDeliveryException(message, ex, status);
        }
        return new NotificationDeliveryException(message, ex);
    }
}
