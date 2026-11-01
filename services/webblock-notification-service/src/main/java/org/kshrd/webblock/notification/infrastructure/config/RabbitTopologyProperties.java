package org.kshrd.webblock.notification.infrastructure.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

import java.time.Duration;

/**
 * Settings under "webblock.notification.rabbitmq".
 *
 * @param declareSenderExchanges     false (default): this service declares only its own queues and their bindings, and never
 *                                   touches the exchanges of the sending services (project.events, provisioning.events,
 *                                   payment.events). true: it also declares them, only for a fresh local broker where no sender
 *                                   has started yet. Declaring them needs the "configure" permission on another service's
 *                                   exchange, and fails with PRECONDITION_FAILED if the sender's settings ever differ,
 *                                   so never use true in uat/prod.
 * @param senderExchangeCheckInterval how often the service checks that the sender exchanges exist (and binds its queues once
 *                                   they do). Only used when the exchanges are not declared here.
 */
@ConfigurationProperties("webblock.notification.rabbitmq")
public record RabbitTopologyProperties(
        @DefaultValue("false") boolean declareSenderExchanges,
        @DefaultValue("15s") Duration senderExchangeCheckInterval) {
}
