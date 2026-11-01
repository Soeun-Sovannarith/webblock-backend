package org.kshrd.webblock.notification.common.domain;

/**
 * Marks an exception that retrying can never fix (bad request, wrong credentials, missing workflow, recipient lookup
 * not configured). The RabbitMQ listener does not retry it: the message goes straight to the dead-letter queue.
 * Anything without this marker is treated as temporary and retried a limited number of times.
 */
public interface PermanentFailure {
}
