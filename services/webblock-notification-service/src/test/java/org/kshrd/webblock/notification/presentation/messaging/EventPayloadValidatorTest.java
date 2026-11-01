package org.kshrd.webblock.notification.presentation.messaging;

import jakarta.validation.Validation;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.notification.application.notification.request.PublishCompletedEvent;
import org.kshrd.webblock.notification.application.notification.request.SourcePublishedEvent;
import org.springframework.amqp.AmqpRejectAndDontRequeueException;

import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class EventPayloadValidatorTest {

    private final EventPayloadValidator validator =
            new EventPayloadValidator(Validation.buildDefaultValidatorFactory().getValidator());

    @Test
    @DisplayName("acceptsAnEventWithAllRequiredFields")
    void acceptsValidEvent() {
        assertThatCode(() -> validator.validate(new SourcePublishedEvent(UUID.randomUUID(), UUID.randomUUID(), null)))
                .doesNotThrowAnyException();
    }

    @Test
    @DisplayName("sendsAnEventWithoutProjectIdStraightToTheDeadLetterQueue")
    void rejectsMissingProjectId() {
        assertThatThrownBy(() -> validator.validate(new SourcePublishedEvent(UUID.randomUUID(), null, null)))
                .isInstanceOf(AmqpRejectAndDontRequeueException.class)
                .hasMessageContaining("projectId");
    }

    @Test
    @DisplayName("rejectsANullPayload")
    void rejectsNullPayload() {
        assertThatThrownBy(() -> validator.validate(null))
                .isInstanceOf(AmqpRejectAndDontRequeueException.class);
    }

    @Test
    @DisplayName("errorTextNamesTheFieldButNeverEchoesTheValue")
    void doesNotEchoValues() {
        var event = new PublishCompletedEvent(UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID(),
                " ", "key-1", "secret-looking-failure-code");

        assertThatThrownBy(() -> validator.validate(event))
                .isInstanceOf(AmqpRejectAndDontRequeueException.class)
                .hasMessageContaining("status")
                .hasMessageNotContaining("secret-looking-failure-code");
    }
}
