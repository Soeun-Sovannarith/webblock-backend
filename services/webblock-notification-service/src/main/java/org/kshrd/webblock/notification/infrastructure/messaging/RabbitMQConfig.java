package org.kshrd.webblock.notification.infrastructure.messaging;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import org.kshrd.webblock.notification.infrastructure.config.RabbitTopologyProperties;
import org.springframework.amqp.core.Binding;
import org.springframework.amqp.core.BindingBuilder;
import org.springframework.amqp.core.Declarable;
import org.springframework.amqp.core.Declarables;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.core.QueueBuilder;
import org.springframework.amqp.core.TopicExchange;
import org.springframework.amqp.support.converter.Jackson2JsonMessageConverter;
import org.springframework.amqp.support.converter.MessageConverter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.ArrayList;
import java.util.List;

/**
 * Notification sends no events, so it owns no exchange. It owns one quorum queue per event it listens to
 * (rabbitmq-events.md: queue "<consumer>.<event>.v<N>", dead-letter queue "<queue>.dlq") and binds it to the SENDER's exchange.
 * Retries (5 attempts, then the dead-letter queue) come from spring.rabbitmq.listener.simple.retry in application.yml.
 */
@Configuration
public class RabbitMQConfig {

    // Sender exchanges: owned and declared by the senders. Only bound to here (declared here too only when asked, see notificationTopology)
    public static final String PROJECT_EXCHANGE = "project.events";
    public static final String PROVISIONING_EXCHANGE = "provisioning.events";
    public static final String PAYMENT_EXCHANGE = "payment.events";

    // Queues owned by this service
    public static final String SOURCE_PUBLISHED_QUEUE = "notification.source_published.v1";
    public static final String PUBLISH_COMPLETED_QUEUE = "notification.publish_completed.v1";
    public static final String HOSTING_ENTITLEMENT_ENDED_QUEUE = "notification.hosting_entitlement_ended.v1";
    public static final String SUBSCRIPTION_ACTIVATED_QUEUE = "notification.subscription_activated.v1";
    public static final String PAYMENT_DUE_QUEUE = "notification.payment_due.v1";

    @Bean
    public Declarables notificationTopology(RabbitTopologyProperties properties) {
        TopicExchange project = new TopicExchange(PROJECT_EXCHANGE, true, false);
        TopicExchange provisioning = new TopicExchange(PROVISIONING_EXCHANGE, true, false);
        TopicExchange payment = new TopicExchange(PAYMENT_EXCHANGE, true, false);

        // The exchanges belong to the sending services, so by default only OUR queues and their bindings are declared.
        // The exchanges are declared here only on request (webblock.notification.rabbitmq.declare-sender-exchanges=true),
        // for a fresh local broker where no sender has started yet.
        List<Declarable> declarables = new ArrayList<>();
        if (properties.declareSenderExchanges()) {
            declarables.addAll(List.of(project, provisioning, payment));
        }
        bind(declarables, SOURCE_PUBLISHED_QUEUE, project, "source_published.v1");
        bind(declarables, PUBLISH_COMPLETED_QUEUE, provisioning, "publish_completed.v1");
        bind(declarables, HOSTING_ENTITLEMENT_ENDED_QUEUE, payment, "hosting_entitlement_ended.v1");
        // payment-service still publishes on its old shared exchange (open question 13): nothing arrives here until it migrates
        bind(declarables, SUBSCRIPTION_ACTIVATED_QUEUE, payment, "subscription_activated.v1");
        // Contract not merged yet: the payment team adds payment.payment_due.v1 (7 days before the due date)
        bind(declarables, PAYMENT_DUE_QUEUE, payment, "payment_due.v1");
        return new Declarables(declarables);
    }

    private static void bind(List<Declarable> out, String queueName, TopicExchange exchange, String routingKey) {
        String dlqName = queueName + ".dlq";
        Queue queue = QueueBuilder.durable(queueName)
                .quorum()
                .deadLetterExchange("")               // default exchange: routes straight to the queue named by the key
                .deadLetterRoutingKey(dlqName)
                .build();
        Queue dlq = QueueBuilder.durable(dlqName).quorum().build();
        Binding binding = BindingBuilder.bind(queue).to(exchange).with(routingKey);
        out.add(queue);
        out.add(dlq);
        out.add(binding);
    }

    /** Spring Boot puts this converter into the listener container factory and the RabbitTemplate. */
    @Bean
    @SuppressWarnings("removal")
    public MessageConverter jackson2JsonMessageConverter() {
        ObjectMapper objectMapper = new ObjectMapper();
        objectMapper.registerModule(new JavaTimeModule());
        objectMapper.disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
        objectMapper.configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false);
        return new Jackson2JsonMessageConverter(objectMapper);
    }
}
