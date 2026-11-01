package org.kshrd.webblock.notification.application.alert.handler;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.kshrd.webblock.notification.TestProperties;
import org.kshrd.webblock.notification.application.alert.request.AlertmanagerWebhookRequest;
import org.kshrd.webblock.notification.application.alert.request.AlertmanagerWebhookRequest.Alert;
import org.kshrd.webblock.notification.application.notification.handler.AdminNotifier;
import org.kshrd.webblock.notification.application.notification.handler.OwnerNotifier;
import org.mockito.ArgumentCaptor;
import org.mockito.InOrder;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

@ExtendWith(MockitoExtension.class)
class HandleAlertmanagerWebhookHandlerTest {

    @Mock
    private OwnerNotifier ownerNotifier;

    @Mock
    private AdminNotifier adminNotifier;

    private final UUID projectId = UUID.randomUUID();

    private HandleAlertmanagerWebhookHandler handler() {
        return new HandleAlertmanagerWebhookHandler(ownerNotifier, adminNotifier, TestProperties.PROPERTIES);
    }

    private static Alert alert(String status, String alertName, Map<String, String> extraLabels) {
        Map<String, String> labels = new HashMap<>(extraLabels);
        labels.put("alertname", alertName);
        return new Alert(status, labels, "2026-10-28T07:00:00Z", "abc123");
    }

    private void send(Alert... alerts) {
        handler().handle(new AlertmanagerWebhookRequest(List.of(alerts)));
    }

    @Test
    @DisplayName("websiteDownTellsTheAdminsThenTheOwner")
    void websiteDownTellsAdminsThenOwner() {
        send(alert("firing", "WebsiteDown", Map.of("project_id", projectId.toString())));

        InOrder order = inOrder(adminNotifier, ownerNotifier);
        order.verify(adminNotifier).notifyAdmins(eq("alertmanager.alert"), eq("WebsiteDown:abc123:2026-10-28T07:00:00Z"),
                any(), eq("crash-admin"), anyMap());
        order.verify(ownerNotifier).notifyOwner(eq("alertmanager.alert"), eq("WebsiteDown:abc123:2026-10-28T07:00:00Z"),
                any(), eq(projectId), eq("crash-owner"), anyMap());
    }

    @Test
    @DisplayName("resourceLimitPassesTheUsageLabelsToKnock")
    @SuppressWarnings("unchecked")
    void resourceLimitPassesUsageLabels() {
        send(alert("firing", "WebsiteResourceLimitExceeded", Map.of(
                "project_id", projectId.toString(), "resource_type", "bandwidth",
                "current_usage", "95", "plan_limit", "100", "instance", "10.0.0.7:9100")));

        ArgumentCaptor<Map<String, Object>> data = ArgumentCaptor.forClass(Map.class);
        verify(ownerNotifier).notifyOwner(any(), any(), any(), eq(projectId), eq("limit-owner"), data.capture());
        verify(adminNotifier).notifyAdmins(any(), any(), any(), eq("limit-admin"), anyMap());
        assertThat(data.getValue())
                .containsEntry("resourceType", "bandwidth")
                .containsEntry("currentUsage", "95")
                .containsEntry("planLimit", "100")
                .containsEntry("alertName", "WebsiteResourceLimitExceeded")
                .doesNotContainKey("instance");   // only listed labels are passed on
    }

    @Test
    @DisplayName("generationStuckAndPlatformServiceDownGoToAdminsOnly")
    void adminOnlyAlerts() {
        send(alert("firing", "GenerationStuck", Map.of()),
                alert("firing", "PlatformServiceDown", Map.of("service", "project-service")));

        verify(adminNotifier).notifyAdmins(any(), any(), any(), eq("stuck-admin"), anyMap());
        verify(adminNotifier).notifyAdmins(any(), any(), any(), eq("down-admin"), anyMap());
        verify(ownerNotifier, never()).notifyOwner(any(), any(), any(), any(), any(), anyMap());
    }

    @Test
    @DisplayName("resolvedAlertsAndUnknownAlertNamesSendNothing")
    void resolvedAndUnknownSendNothing() {
        send(alert("resolved", "WebsiteDown", Map.of("project_id", projectId.toString())),
                alert("firing", "SomethingElse", Map.of()));

        verify(adminNotifier, never()).notifyAdmins(any(), any(), any(), any(), anyMap());
        verify(ownerNotifier, never()).notifyOwner(any(), any(), any(), any(), any(), anyMap());
    }

    @Test
    @DisplayName("withoutAValidProjectIdOnlyTheAdminsAreTold")
    void badProjectIdTellsAdminsOnly() {
        send(alert("firing", "WebsiteDown", Map.of("project_id", "not-a-uuid")));

        verify(adminNotifier).notifyAdmins(any(), any(), any(), eq("crash-admin"), anyMap());
        verify(ownerNotifier, never()).notifyOwner(any(), any(), any(), any(), any(), anyMap());
    }

    @Test
    @DisplayName("alertWithoutFingerprintIsSkipped")
    void alertWithoutFingerprintIsSkipped() {
        send(new Alert("firing", Map.of("alertname", "GenerationStuck"), "2026-10-28T07:00:00Z", null));

        verify(adminNotifier, never()).notifyAdmins(any(), any(), any(), any(), anyMap());
    }

    @Test
    @DisplayName("theSameIncidentSentTwiceGetsTheSameKeyAndId")
    void sameIncidentSameKey() {
        Alert first = alert("firing", "GenerationStuck", Map.of());
        send(first, first);

        ArgumentCaptor<String> keys = ArgumentCaptor.forClass(String.class);
        ArgumentCaptor<UUID> ids = ArgumentCaptor.forClass(UUID.class);
        verify(adminNotifier, times(2)).notifyAdmins(any(), keys.capture(), ids.capture(), any(), anyMap());
        // Same key -> same Knock Idempotency-Key -> Knock drops the repeat
        assertThat(keys.getAllValues()).containsOnly("GenerationStuck:abc123:2026-10-28T07:00:00Z");
        assertThat(ids.getAllValues().get(0)).isEqualTo(ids.getAllValues().get(1));
    }
}
