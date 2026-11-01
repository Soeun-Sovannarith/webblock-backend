package org.kshrd.webblock.notification.presentation.messaging;

import jakarta.validation.ConstraintViolation;
import jakarta.validation.Validator;
import org.springframework.amqp.AmqpRejectAndDontRequeueException;
import org.springframework.stereotype.Component;

import java.util.Set;

/**
 * Checks an incoming event. A bad event can never become good by retrying, so it goes straight to the dead-letter queue.
 * The error text names the fields only, never the values.
 */
@Component
public class EventPayloadValidator {

    private final Validator validator;

    public EventPayloadValidator(Validator validator) {
        this.validator = validator;
    }

    public <T> void validate(T event) {
        if (event == null) {
            throw new AmqpRejectAndDontRequeueException("Event payload is null; routing to DLQ.");
        }
        Set<ConstraintViolation<T>> violations = validator.validate(event);
        if (!violations.isEmpty()) {
            StringBuilder sb = new StringBuilder("Invalid ").append(event.getClass().getSimpleName()).append(": ");
            for (ConstraintViolation<T> v : violations) {
                sb.append(v.getPropertyPath()).append(" ").append(v.getMessage()).append("; ");
            }
            throw new AmqpRejectAndDontRequeueException(sb.toString());
        }
    }
}
