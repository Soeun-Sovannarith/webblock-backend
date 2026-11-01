package org.kshrd.webblock.project.infrastructure.messaging.messaging;

import io.namastack.outbox.OutboxPayloadSerializer;
import io.namastack.outbox.rabbit.RabbitOutboxRouting;
import io.namastack.outbox.routing.selector.OutboxPayloadSelector;
import javax.sql.DataSource;
import org.kshrd.webblock.project.application.messaging.model.IntegrationEvent;
import org.springframework.amqp.core.BindingBuilder;
import org.springframework.amqp.core.Declarables;
import org.springframework.amqp.core.ExchangeBuilder;
import org.springframework.amqp.core.FanoutExchange;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.core.QueueBuilder;
import org.springframework.amqp.core.TopicExchange;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.modulith.events.EventExternalizationConfiguration;
import org.springframework.modulith.events.RoutingTarget;
import org.springframework.scheduling.concurrent.ThreadPoolTaskScheduler;

/**
 * The outbox wiring of decision 0001 ({@code .claude/docs/decisions/0001-outbox-with-modulith-and-namastack.md}).
 * The matching properties are under {@code spring.modulith.events} and {@code namastack.outbox} in {@code
 * application.yml}.
 */
@Configuration(proxyBeanMethods = false)
class MessagingConfig {

    static final String EXCHANGE = "project.events";
    static final String UNROUTED = "project.events.unrouted";

    /**
     * What this service declares on the broker (Spring AMQP's admin declares it on connection): its topic exchange
     * {@code project.events}, whose alternate exchange {@code project.events.unrouted} (fanout) keeps every event no
     * consumer queue is bound for in the quorum queue {@code project.events.unrouted}. The outbox does not fail on
     * unroutable events (that would block the project's later events), so this is where they are kept instead of being
     * dropped. Runbook: {@code .claude/docs/runbooks/outbox.md}.
     */
    @Bean
    Declarables projectEventsTopology() {
        TopicExchange events = ExchangeBuilder.topicExchange(EXCHANGE)
                .durable(true)
                .alternate(UNROUTED)
                .build();
        FanoutExchange unrouted =
                ExchangeBuilder.fanoutExchange(UNROUTED).durable(true).build();
        Queue unroutedQueue = QueueBuilder.durable(UNROUTED).quorum().build();
        return new Declarables(
                events,
                unrouted,
                unroutedQueue,
                BindingBuilder.bind(unroutedQueue).to(unrouted));
    }

    /**
     * Stores outbox payloads with a mapper that ignores unknown properties on read. Namastack's own serializer is
     * {@code @ConditionalOnMissingBean} and would use the strict web mapper.
     */
    @Bean
    OutboxPayloadSerializer outboxPayloadSerializer() {
        return new JsonOutboxPayloadSerializer();
    }

    /**
     * Gauges over the outbox tables, on {@code /actuator/prometheus} (runbook: {@code
     * .claude/docs/runbooks/outbox.md}).
     */
    @Bean
    OutboxMetrics outboxMetrics(DataSource dataSource) {
        return new OutboxMetrics(dataSource);
    }

    /**
     * Modulith side: every {@link IntegrationEvent} is externalized, and its outbox key is the project ID. In outbox mode
     * Modulith passes only the key to Namastack; the exchange and routing key come from {@link #rabbitOutboxRouting}.
     */
    @Bean
    EventExternalizationConfiguration eventExternalizationConfiguration() {
        return EventExternalizationConfiguration.externalizing()
                .selectByType(IntegrationEvent.class)
                .route(
                        IntegrationEvent.class,
                        event -> RoutingTarget.forTarget(EXCHANGE).andKey(IntegrationEvents.outboxKey(event)))
                .build();
    }

    /**
     * Namastack side: an {@link IntegrationEvent} record goes to {@code project.events} with routing key {@code
     * <event>.v<N>}, as a ready AMQP message (our {@code type}, {@code message_id} and plain JSON body). Nothing else is
     * sent to the broker: there is no default route, so local jobs never reach the Rabbit handler.
     */
    @Bean
    RabbitOutboxRouting rabbitOutboxRouting() {
        return RabbitOutboxRouting.builder()
                .route(OutboxPayloadSelector.type(IntegrationEvent.class), route -> {
                    route.target(EXCHANGE);
                    route.key((payload, metadata) -> IntegrationEvents.routingKey((IntegrationEvent) payload));
                    route.mapping((payload, metadata) -> IntegrationEvents.toMessage((IntegrationEvent) payload));
                })
                .build();
    }

    /**
     * One platform thread runs both the outbox polling cycle and the partition rebalance, so an instance never releases a
     * partition while it is still sending that partition's records. Namastack's default under virtual threads runs the
     * rebalance on a separate thread (spike S1, question (d)).
     */
    @Bean(name = "outboxDefaultScheduler", autowireCandidate = false)
    ThreadPoolTaskScheduler outboxDefaultScheduler() {
        ThreadPoolTaskScheduler scheduler = new ThreadPoolTaskScheduler();
        scheduler.setPoolSize(1);
        scheduler.setThreadNamePrefix("outbox-scheduler-");
        return scheduler;
    }
}
