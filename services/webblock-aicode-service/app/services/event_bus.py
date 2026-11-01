"""RabbitMQ events with the Project Service, by the platform's message conventions (Project's
.claude/docs/contracts/service-communication.md, "Message conventions"):

- one durable topic exchange per producing service: this service publishes to `ai_codegen.events` and consumes from
  `project.events`, which the Project Service declares (with an alternate exchange), so it is declared passively here;
- the routing key is `<event>.v<N>`; snake_case bodies with the envelope (schemas/events.py); AMQP properties `type`
  (= message_type), `message_id`, content type `application/json`, UTF-8, persistent;
- the consumer owns its queues `ai_codegen.<event>.v<N>`: quorum queues with a delivery limit, each with its own
  dead-letter queue `<queue>.dlq`.
"""
import json
import logging
import os
import threading
import time
import uuid
from typing import Any
import pika
from pydantic import ValidationError
from app.core.config import settings
from app.schemas.events import GenerationCancelledEvent, GenerationCompletedEvent, GenerationRequestedEvent

log = logging.getLogger("webblock.event_bus")

EXCHANGE_NAME = "ai_codegen.events"  # ours: we publish here
PROJECT_EXCHANGE = "project.events"  # the Project Service's: we consume from here
COMPLETED_ROUTING_KEY = "generation_completed.v1"
QUEUE_GENERATION_REQUESTED = "ai_codegen.generation_requested.v1"
QUEUE_GENERATION_CANCELLED = "ai_codegen.generation_cancelled.v1"
# A message that keeps failing (Project unreachable, another run active) is retried this many times, RETRY_DELAY
# seconds apart, then parked in the dead-letter queue. Project's generation deadline re-publishes a request that has
# no run (contract 2.5), so a parked request is not lost.
DELIVERY_LIMIT = 10
RETRY_DELAY_SECONDS = 5


class RetryLater(Exception):
    """Raised by an event handler when it cannot finish now but may later: the message is redelivered."""


def uuid7() -> uuid.UUID:
    """A UUIDv7 (RFC 9562): 48 bits of Unix milliseconds, then random bits. The platform's message_id format."""
    value = (time.time_ns() // 1_000_000) << 80 | int.from_bytes(os.urandom(10), "big")
    value = (value & ~(0xF << 76)) | (0x7 << 76)  # version 7
    value = (value & ~(0x3 << 62)) | (0x2 << 62)  # RFC 9562 variant
    return uuid.UUID(int=value)


def _get_connection_params() -> pika.ConnectionParameters:
    credentials = pika.PlainCredentials(settings.rabbitmq_username, settings.rabbitmq_password)
    return pika.ConnectionParameters(
        host=settings.rabbitmq_host,
        port=settings.rabbitmq_port,
        credentials=credentials,
        connection_attempts=2,
        retry_delay=1,
        socket_timeout=3,
    )


def publish_event(routing_key: str, event: GenerationCompletedEvent) -> bool:
    """Publish one of our events to ai_codegen.events, with publisher confirms. Best-effort: returns False and logs
    a warning when the broker is unreachable or refuses the message; never raises."""
    body = event.model_dump_json()
    try:
        with pika.BlockingConnection(_get_connection_params()) as conn:
            channel = conn.channel()
            channel.confirm_delivery()
            channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="topic", durable=True)
            channel.basic_publish(
                exchange=EXCHANGE_NAME,
                routing_key=routing_key,
                body=body.encode("utf-8"),
                properties=pika.BasicProperties(
                    content_type="application/json",
                    content_encoding="utf-8",
                    delivery_mode=pika.DeliveryMode.Persistent,
                    type=event.message_type,
                    message_id=str(event.message_id),
                ),
            )
            log.info("Published %s (message %s)", event.message_type, event.message_id)
            return True
    except Exception as e:
        log.warning("Could not publish %s to RabbitMQ: %s", event.message_type, e)
        return False


def publish_generation_completed(generation_request_id, generation_run_id, project_id, status: str) -> bool:
    """Outbound event: ai_codegen.generation_completed.v1, when a request's final run ends (status SUCCEEDED or
    FAILED). Project then reads the run from GET /internal/v1/generation-runs/{id}."""
    event = GenerationCompletedEvent(
        message_id=uuid7(),
        correlation_id=str(generation_request_id),
        project_id=project_id,
        generation_request_id=generation_request_id,
        generation_run_id=generation_run_id,
        outcome="succeeded" if status == "SUCCEEDED" else "failed",
    )
    return publish_event(COMPLETED_ROUTING_KEY, event)


def _declare_queue(channel, queue: str, routing_key: str) -> None:
    dlq = f"{queue}.dlq"
    channel.queue_declare(queue=dlq, durable=True, arguments={"x-queue-type": "quorum"})
    channel.queue_declare(queue=queue, durable=True, arguments={
        "x-queue-type": "quorum",
        "x-delivery-limit": DELIVERY_LIMIT,
        "x-dead-letter-exchange": "",
        "x-dead-letter-routing-key": dlq,
    })
    channel.queue_bind(exchange=PROJECT_EXCHANGE, queue=queue, routing_key=routing_key)


class EventConsumerThread(threading.Thread):
    def __init__(self):
        super().__init__(name="EventConsumerThread", daemon=True)
        self._stop_event = threading.Event()
        self._connection: pika.BlockingConnection | None = None
        self._channel: Any = None

    def run(self):
        log.info("Starting RabbitMQ consumer thread for ai_codegen...")
        while not self._stop_event.is_set():
            try:
                self._connection = pika.BlockingConnection(_get_connection_params())
                self._channel = self._connection.channel()
                # The Project Service owns project.events; a passive declare fails until it has declared it.
                self._channel.exchange_declare(exchange=PROJECT_EXCHANGE, exchange_type="topic", passive=True)
                _declare_queue(self._channel, QUEUE_GENERATION_REQUESTED, "generation_requested.v1")
                _declare_queue(self._channel, QUEUE_GENERATION_CANCELLED, "generation_cancelled.v1")

                self._channel.basic_qos(prefetch_count=1)
                self._channel.basic_consume(queue=QUEUE_GENERATION_REQUESTED, on_message_callback=self._on_generation_requested)
                self._channel.basic_consume(queue=QUEUE_GENERATION_CANCELLED, on_message_callback=self._on_generation_cancelled)

                log.info("RabbitMQ consumer connected and listening on %s and %s",
                         QUEUE_GENERATION_REQUESTED, QUEUE_GENERATION_CANCELLED)
                while not self._stop_event.is_set():
                    self._connection.process_data_events(time_limit=1)

            except Exception as e:
                if not self._stop_event.is_set():
                    log.warning("RabbitMQ consumer disconnected (%s); retrying in 5s...", e)
                    time.sleep(5)
            finally:
                self._cleanup()

    def _on_generation_requested(self, ch, method, properties, body):
        from app.services.harness import handle_generation_requested_event
        _handle(ch, method, body, GenerationRequestedEvent, handle_generation_requested_event)

    def _on_generation_cancelled(self, ch, method, properties, body):
        from app.services.harness import handle_generation_cancelled_event
        _handle(ch, method, body, GenerationCancelledEvent, handle_generation_cancelled_event)

    def _cleanup(self):
        try:
            if self._connection and self._connection.is_open:
                self._connection.close()
        except Exception:
            pass

    def stop(self):
        self._stop_event.set()
        if self._connection and self._connection.is_open:
            try:
                self._connection.add_callback_threadsafe(self._cleanup)
            except Exception:
                pass


def _handle(ch, method, body: bytes, model, handler) -> None:
    """Acknowledge only after the handler's effect is committed. A malformed message goes straight to the dead-letter
    queue; a handler that cannot finish now is redelivered after RETRY_DELAY_SECONDS, up to DELIVERY_LIMIT times."""
    message_type = model.model_fields["message_type"].default
    try:
        event = model.model_validate(json.loads(body.decode("utf-8")))
    except (ValueError, ValidationError) as e:
        log.error("Malformed %s, sent to the dead-letter queue: %s", message_type, e)
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return
    try:
        result = handler(event)
        log.info("%s for request %s: %s", message_type, event.generation_request_id, result)
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as e:
        if isinstance(e, RetryLater):
            log.warning("%s for request %s will be retried: %s", message_type, event.generation_request_id, e)
        else:
            log.exception("Error processing %s for request %s", message_type, event.generation_request_id)
        time.sleep(RETRY_DELAY_SECONDS)
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)


_consumer_instance: EventConsumerThread | None = None

def start_consumer() -> None:
    global _consumer_instance
    if _consumer_instance is None or not _consumer_instance.is_alive():
        _consumer_instance = EventConsumerThread()
        _consumer_instance.start()

def stop_consumer() -> None:
    global _consumer_instance
    if _consumer_instance:
        _consumer_instance.stop()
        _consumer_instance = None
