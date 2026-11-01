package org.kshrd.webblock.notification.presentation.messaging;

import jakarta.validation.Validation;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.kshrd.webblock.notification.application.notification.handler.HandleHostingEntitlementEndedHandler;
import org.kshrd.webblock.notification.application.notification.handler.HandlePaymentDueHandler;
import org.kshrd.webblock.notification.application.notification.handler.HandlePublishCompletedHandler;
import org.kshrd.webblock.notification.application.notification.handler.HandleSourcePublishedHandler;
import org.kshrd.webblock.notification.application.notification.handler.HandleSubscriptionActivatedHandler;
import org.kshrd.webblock.notification.application.notification.request.HostingEntitlementEndedEvent;
import org.kshrd.webblock.notification.application.notification.request.PaymentDueEvent;
import org.kshrd.webblock.notification.application.notification.request.PublishCompletedEvent;
import org.kshrd.webblock.notification.application.notification.request.SourcePublishedEvent;
import org.kshrd.webblock.notification.application.notification.request.SubscriptionActivatedEvent;
import org.springframework.amqp.AmqpRejectAndDontRequeueException;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;
import java.util.function.BiConsumer;
import java.util.function.Consumer;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;

/**
 * What every @RabbitListener consumer must do, with the real validator and a mock handler:
 * pass a good event to its handler once, reject a bad event before the handler sees it, and never swallow a handler failure
 * (a swallowed failure would be acknowledged and the notification lost).
 */
class ConsumersTest {

    private static final EventPayloadValidator VALIDATOR =
            new EventPayloadValidator(Validation.buildDefaultValidatorFactory().getValidator());

    /** One consumer: how to call it, a valid and an invalid event, and how to call its handler with an event. */
    private record Case<H, E>(String name, H handler, Consumer<E> consume, E valid, E invalid, BiConsumer<H, E> handle) {
    }

    private static <H, E> Arguments consumer(String name, H handler, Consumer<E> consume, E valid, E invalid, BiConsumer<H, E> handle) {
        return Arguments.of(name, new Case<>(name, handler, consume, valid, invalid, handle));
    }

    static Stream<Arguments> consumers() {
        UUID id = UUID.randomUUID();
        var sourceHandler = mock(HandleSourcePublishedHandler.class);
        var publishHandler = mock(HandlePublishCompletedHandler.class);
        var hostingHandler = mock(HandleHostingEntitlementEndedHandler.class);
        var subscriptionHandler = mock(HandleSubscriptionActivatedHandler.class);
        var dueHandler = mock(HandlePaymentDueHandler.class);
        return Stream.of(
                consumer("SourcePublishedConsumer", sourceHandler,
                        (Consumer<SourcePublishedEvent>) new SourcePublishedConsumer(sourceHandler, VALIDATOR)::consume,
                        new SourcePublishedEvent(id, id, null), new SourcePublishedEvent(id, null, null),
                        (BiConsumer<HandleSourcePublishedHandler, SourcePublishedEvent>) HandleSourcePublishedHandler::handle),
                consumer("PublishCompletedConsumer", publishHandler,
                        (Consumer<PublishCompletedEvent>) new PublishCompletedConsumer(publishHandler, VALIDATOR)::consume,
                        new PublishCompletedEvent(id, id, id, "LIVE", "k", null), new PublishCompletedEvent(id, id, id, " ", "k", null),
                        (BiConsumer<HandlePublishCompletedHandler, PublishCompletedEvent>) HandlePublishCompletedHandler::handle),
                consumer("HostingEntitlementEndedConsumer", hostingHandler,
                        (Consumer<HostingEntitlementEndedEvent>) new HostingEntitlementEndedConsumer(hostingHandler, VALIDATOR)::consume,
                        new HostingEntitlementEndedEvent(id, id, "k"), new HostingEntitlementEndedEvent(id, id, ""),
                        (BiConsumer<HandleHostingEntitlementEndedHandler, HostingEntitlementEndedEvent>) HandleHostingEntitlementEndedHandler::handle),
                consumer("SubscriptionActivatedConsumer", subscriptionHandler,
                        (Consumer<SubscriptionActivatedEvent>) new SubscriptionActivatedConsumer(subscriptionHandler, VALIDATOR)::consume,
                        new SubscriptionActivatedEvent(id, "k", id, id, id), new SubscriptionActivatedEvent(id, "k", id, id, null),
                        (BiConsumer<HandleSubscriptionActivatedHandler, SubscriptionActivatedEvent>) HandleSubscriptionActivatedHandler::handle),
                consumer("PaymentDueConsumer", dueHandler,
                        (Consumer<PaymentDueEvent>) new PaymentDueConsumer(dueHandler, VALIDATOR)::consume,
                        new PaymentDueEvent(id, "k", id, id, id, new BigDecimal("9.90"), Instant.parse("2026-11-30T00:00:00Z")),
                        new PaymentDueEvent(id, "k", id, id, id, null, Instant.parse("2026-11-30T00:00:00Z")),
                        (BiConsumer<HandlePaymentDueHandler, PaymentDueEvent>) HandlePaymentDueHandler::handle));
    }

    @SuppressWarnings("unchecked")
    private static Case<Object, Object> cast(Case<?, ?> c) {
        return (Case<Object, Object>) c;
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("consumers")
    @DisplayName("passesAValidEventToItsHandlerExactlyOnce")
    void validEventReachesTheHandler(String name, Case<?, ?> raw) {
        Case<Object, Object> c = cast(raw);
        org.mockito.Mockito.clearInvocations(c.handler());

        c.consume().accept(c.valid());

        c.handle().accept(verify(c.handler(), times(1)), c.valid());
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("consumers")
    @DisplayName("rejectsAnInvalidEventWithoutRequeueAndNeverCallsTheHandler")
    void invalidEventIsRejected(String name, Case<?, ?> raw) {
        Case<Object, Object> c = cast(raw);
        org.mockito.Mockito.clearInvocations(c.handler());

        assertThatThrownBy(() -> c.consume().accept(c.invalid())).isInstanceOf(AmqpRejectAndDontRequeueException.class);

        verifyNoInteractions(c.handler());
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("consumers")
    @DisplayName("lettersAHandlerFailureOutSoTheMessageIsNotAcknowledgedAndLost")
    void handlerFailureIsNotSwallowed(String name, Case<?, ?> raw) {
        Case<Object, Object> c = cast(raw);
        org.mockito.Mockito.clearInvocations(c.handler());
        var failure = new IllegalStateException("downstream failed");
        c.handle().accept(doThrow(failure).when(c.handler()), c.valid());

        assertThatThrownBy(() -> c.consume().accept(c.valid())).isSameAs(failure);
        org.mockito.Mockito.reset(c.handler());
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("consumers")
    @DisplayName("hasNoStateSoRedeliveringTheSameEventCallsTheHandlerAgain")
    void redeliveryIsPassedOnNotDroppedByTheConsumer(String name, Case<?, ?> raw) {
        // Duplicate suppression is not the consumer's job: it comes from the Idempotency-Key at Knock (DuplicateDeliveryTest).
        Case<Object, Object> c = cast(raw);
        org.mockito.Mockito.clearInvocations(c.handler());

        c.consume().accept(c.valid());
        c.consume().accept(c.valid());

        c.handle().accept(verify(c.handler(), times(2)), c.valid());
    }
}
