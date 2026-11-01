package org.kshrd.webblock.notification.infrastructure.messaging;

import io.micrometer.core.instrument.MeterRegistry;
import org.kshrd.webblock.notification.infrastructure.config.RabbitTopologyProperties;
import org.springframework.amqp.AmqpException;
import org.springframework.amqp.core.AmqpAdmin;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.SchedulingConfigurer;
import org.springframework.scheduling.config.ScheduledTaskRegistrar;

/**
 * Runs {@link SenderExchangeWatcher} on a fixed delay. Only when the listeners start automatically: a context that does not
 * consume messages (the tests) has nothing to bind and must never touch a broker.
 * Skipped as well when this service declares the sender exchanges itself (declare-sender-exchanges=true), as it then
 * creates them and they always exist.
 */
@Configuration
@EnableScheduling
@ConditionalOnProperty(name = "spring.rabbitmq.listener.simple.auto-startup", havingValue = "true", matchIfMissing = true)
@ConditionalOnProperty(name = "webblock.notification.rabbitmq.declare-sender-exchanges", havingValue = "false", matchIfMissing = true)
public class SenderExchangeWatcherConfig implements SchedulingConfigurer {

    private final SenderExchangeWatcher watcher;
    private final RabbitTopologyProperties properties;

    public SenderExchangeWatcherConfig(RabbitTemplate rabbitTemplate, AmqpAdmin amqpAdmin, MeterRegistry registry,
                                       RabbitTopologyProperties properties) {
        this.properties = properties;
        this.watcher = new SenderExchangeWatcher(new SenderExchangeWatcher.Broker() {
            @Override
            public SenderExchangeWatcher.ExchangeState exchange(String name) {
                try {
                    // A passive declare asks "does it exist?" and needs no more than read permission; it never creates anything
                    rabbitTemplate.execute(channel -> channel.exchangeDeclarePassive(name));
                    return SenderExchangeWatcher.ExchangeState.PRESENT;
                } catch (AmqpException ex) {
                    return SenderExchangeWatcher.classify(ex);
                }
            }

            @Override
            public void declareOwnTopology() {
                amqpAdmin.initialize();
            }
        }, registry);
    }

    @Bean
    SenderExchangeWatcher senderExchangeWatcher() {
        return watcher;
    }

    @Override
    public void configureTasks(ScheduledTaskRegistrar registrar) {
        registrar.addFixedDelayTask(watcher::check, properties.senderExchangeCheckInterval());
    }
}
