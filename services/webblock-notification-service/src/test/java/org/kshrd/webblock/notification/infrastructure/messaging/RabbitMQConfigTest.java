package org.kshrd.webblock.notification.infrastructure.messaging;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.amqp.core.Binding;
import org.kshrd.webblock.notification.infrastructure.config.RabbitTopologyProperties;
import org.springframework.amqp.core.Declarable;
import org.springframework.amqp.core.Exchange;
import org.springframework.amqp.core.Queue;

import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Checks the queue arguments and bindings we declare. It does NOT prove that a real broker moves a rejected message
 * into the ".dlq" queue: that needs a RabbitMQ broker and is not covered by any test here.
 */
class RabbitMQConfigTest {

    /** The default: this service declares only its own queues and bindings. */
    private final List<Declarable> declarables = topology(false);

    private static List<Declarable> topology(boolean declareSenderExchanges) {
        return List.copyOf(new RabbitMQConfig()
                .notificationTopology(new RabbitTopologyProperties(declareSenderExchanges, Duration.ofSeconds(15))).getDeclarables());
    }

    private <T> List<T> of(Class<T> type) {
        return declarables.stream().filter(type::isInstance).map(type::cast).toList();
    }

    @Test
    @DisplayName("declaresNoSenderExchangeByDefaultBecauseTheyBelongToTheSendingServices")
    void doesNotDeclareTheSendersExchangesByDefault() {
        assertThat(of(Exchange.class)).isEmpty();
        // the bindings still point at the senders' exchanges, so messages reach our queues once the senders created them
        assertThat(of(Binding.class)).extracting(Binding::getExchange).containsOnly(
                RabbitMQConfig.PROJECT_EXCHANGE, RabbitMQConfig.PROVISIONING_EXCHANGE, RabbitMQConfig.PAYMENT_EXCHANGE);
        assertThat(of(Queue.class)).hasSize(10);
    }

    @Test
    @DisplayName("declaresTheSendersExchangesOnlyWhenAskedForAFreshLocalBroker")
    void declaresTheSendersExchangesWhenAsked() {
        List<Declarable> withExchanges = topology(true);

        List<Exchange> exchanges = withExchanges.stream().filter(Exchange.class::isInstance).map(Exchange.class::cast).toList();
        assertThat(exchanges).extracting(Exchange::getName).containsExactlyInAnyOrder(
                RabbitMQConfig.PROJECT_EXCHANGE, RabbitMQConfig.PROVISIONING_EXCHANGE, RabbitMQConfig.PAYMENT_EXCHANGE);
        assertThat(exchanges).allSatisfy(e -> {
            assertThat(e.getType()).isEqualTo("topic");
            assertThat(e.isDurable()).isTrue();
            assertThat(e.isAutoDelete()).isFalse();
        });
        // queues and bindings are the same either way
        assertThat(withExchanges.stream().filter(Binding.class::isInstance).count()).isEqualTo(of(Binding.class).size());
    }

    private static Stream<String> mainQueues() {
        return Stream.of(RabbitMQConfig.SOURCE_PUBLISHED_QUEUE, RabbitMQConfig.PUBLISH_COMPLETED_QUEUE,
                RabbitMQConfig.HOSTING_ENTITLEMENT_ENDED_QUEUE, RabbitMQConfig.SUBSCRIPTION_ACTIVATED_QUEUE,
                RabbitMQConfig.PAYMENT_DUE_QUEUE);
    }

    private Queue queue(String name) {
        return of(Queue.class).stream().filter(q -> q.getName().equals(name)).findFirst()
                .orElseThrow(() -> new AssertionError("queue not declared: " + name));
    }

    @Test
    @DisplayName("declaresEveryEventQueueAsADurableQuorumQueueThatDeadLettersToItsOwnDlq")
    void everyQueueDeadLettersToItsOwnDlq() {
        mainQueues().forEach(name -> {
            Queue main = queue(name);
            Queue dlq = queue(name + ".dlq");

            assertThat(main.isDurable()).as(name + " durable").isTrue();
            Map<String, Object> args = main.getArguments();
            assertThat(args).containsEntry("x-queue-type", "quorum")
                    .containsEntry("x-dead-letter-exchange", "")
                    .containsEntry("x-dead-letter-routing-key", name + ".dlq");
            assertThat(dlq.isDurable()).as(name + ".dlq durable").isTrue();
            assertThat(dlq.getArguments()).containsEntry("x-queue-type", "quorum");
            // a dead letter must not loop: the DLQ itself has no dead-letter route
            assertThat(dlq.getArguments()).doesNotContainKey("x-dead-letter-routing-key");
        });
    }

    @Test
    @DisplayName("bindsEachQueueToTheSendersExchangeWithTheVersionedRoutingKey")
    void bindingsFollowTheContractNames() {
        Map<String, String> expected = Map.of(
                RabbitMQConfig.SOURCE_PUBLISHED_QUEUE, RabbitMQConfig.PROJECT_EXCHANGE + "|source_published.v1",
                RabbitMQConfig.PUBLISH_COMPLETED_QUEUE, RabbitMQConfig.PROVISIONING_EXCHANGE + "|publish_completed.v1",
                RabbitMQConfig.HOSTING_ENTITLEMENT_ENDED_QUEUE, RabbitMQConfig.PAYMENT_EXCHANGE + "|hosting_entitlement_ended.v1",
                RabbitMQConfig.SUBSCRIPTION_ACTIVATED_QUEUE, RabbitMQConfig.PAYMENT_EXCHANGE + "|subscription_activated.v1",
                RabbitMQConfig.PAYMENT_DUE_QUEUE, RabbitMQConfig.PAYMENT_EXCHANGE + "|payment_due.v1");

        Map<String, String> actual = new java.util.HashMap<>();
        for (Binding b : of(Binding.class)) {
            actual.put(b.getDestination(), b.getExchange() + "|" + b.getRoutingKey());
        }

        assertThat(actual).containsExactlyInAnyOrderEntriesOf(expected);
    }

    @Test
    @DisplayName("followsTheQueueNamingRuleConsumerEventVersion")
    void queueNames() {
        mainQueues().forEach(name -> assertThat(name).matches("notification\\.[a-z_]+\\.v\\d+"));
    }
}
