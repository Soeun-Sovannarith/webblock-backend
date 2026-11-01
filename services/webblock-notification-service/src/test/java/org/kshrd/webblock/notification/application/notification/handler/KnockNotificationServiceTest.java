package org.kshrd.webblock.notification.application.notification.handler;

import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.kshrd.webblock.notification.application.notification.request.NotificationEventDto;
import org.kshrd.webblock.notification.common.domain.NotificationDeliveryException;
import org.kshrd.webblock.notification.common.domain.PermanentNotificationDeliveryException;
import org.kshrd.webblock.notification.infrastructure.client.KnockDeliveryClient;
import org.kshrd.webblock.notification.infrastructure.observability.NotificationMetrics;
import org.mockito.ArgumentCaptor;
import org.mockito.InOrder;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * What the service itself does: identify, then trigger, with the event context in the data and the key passed on.
 * What is sent to the Knock SDK, and how its errors are classified, is tested in KnockSdkDeliveryClientTest.
 */
@ExtendWith(MockitoExtension.class)
class KnockNotificationServiceTest {

    @Mock
    private KnockDeliveryClient knock;

    private final SimpleMeterRegistry registry = new SimpleMeterRegistry();
    private KnockNotificationService service;

    @BeforeEach
    void setUp() {
        service = new KnockNotificationService(knock, new NotificationMetrics(registry));
    }

    private double deliveries(String outcome) {
        return registry.get(NotificationMetrics.DELIVERY).tags("workflow", "project.source_published.v1", "outcome", outcome)
                .counter().count();
    }

    private final NotificationEventDto event = new NotificationEventDto(
            "evt-123", "project.source_published.v1", "OWNER", "user-456", "owner@example.com", "John Doe",
            Map.of("projectId", "p-999"));

    @Test
    @DisplayName("Successfully processes notification: identifies user and triggers workflow")
    void shouldProcessNotificationSuccessfully() {
        when(knock.triggerWorkflow(anyString(), anyString(), anyMap(), anyString())).thenReturn("run-789");

        service.processNotification(event, "msg.v1:key-1:wf:user-456");

        InOrder order = inOrder(knock);
        order.verify(knock).identifyRecipient("user-456", "John Doe", "owner@example.com");
        ArgumentCaptor<Map<String, Object>> data = mapCaptor();
        order.verify(knock).triggerWorkflow(eq("project.source_published.v1"), eq("user-456"), data.capture(),
                eq("msg.v1:key-1:wf:user-456"));
        // the event's own data plus the context the Knock templates can use
        assertThat(data.getValue()).containsEntry("projectId", "p-999")
                .containsEntry("eventId", "evt-123")
                .containsEntry("recipientRole", "OWNER");
        assertThat(deliveries("success")).isEqualTo(1);
    }

    @Test
    @DisplayName("Throws NotificationDeliveryException when Knock call fails, and does not trigger the workflow")
    void shouldNotTriggerTheWorkflowWhenIdentifyFails() {
        var failure = new NotificationDeliveryException("Failed to identify user in Knock: user-456", new RuntimeException("Connection timeout"));
        doThrow(failure).when(knock).identifyRecipient(anyString(), anyString(), anyString());

        assertThatThrownBy(() -> service.processNotification(event, "k1")).isSameAs(failure);

        verify(knock, never()).triggerWorkflow(any(), any(), any(), any());
        assertThat(deliveries("temporary_failure")).isEqualTo(1);
    }

    @Test
    @DisplayName("letsAPermanentTriggerFailureOutUnchangedSoTheMessageIsDeadLettered")
    void shouldPassOnAPermanentTriggerFailure() {
        var failure = new PermanentNotificationDeliveryException("Failed to trigger Knock workflow: wf", new RuntimeException(), 404);
        when(knock.triggerWorkflow(anyString(), anyString(), anyMap(), anyString())).thenThrow(failure);

        assertThatThrownBy(() -> service.processNotification(event, "k1")).isSameAs(failure);
        assertThat(deliveries("permanent_failure")).isEqualTo(1);
    }

    @Test
    @DisplayName("countsAnUnexpectedErrorAsATemporaryFailureBecauseTheListenerRetriesIt")
    void shouldCountUnknownErrorsAsTemporary() {
        doThrow(new IllegalStateException("boom")).when(knock).identifyRecipient(anyString(), anyString(), anyString());

        assertThatThrownBy(() -> service.processNotification(event, "k1")).isInstanceOf(IllegalStateException.class);

        assertThat(deliveries("temporary_failure")).isEqualTo(1);
    }

    @Test
    @DisplayName("rejectsAMissingEventOrKey")
    void shouldRejectNulls() {
        assertThatThrownBy(() -> service.processNotification(null, "k1")).isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> service.processNotification(event, null)).isInstanceOf(NullPointerException.class);
    }

    @SuppressWarnings({"unchecked", "rawtypes"})
    private static ArgumentCaptor<Map<String, Object>> mapCaptor() {
        return (ArgumentCaptor) ArgumentCaptor.forClass(Map.class);
    }
}
