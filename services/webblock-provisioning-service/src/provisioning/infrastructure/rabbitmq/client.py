"""The broker client (decision 0005 sections 3, 4 and 6) on aio-pika.

- ``BrokerClient.run`` keeps one connection open, declaring or checking the topology on every new
  connection (an exchange owned by another service is only ever checked, decision 0022 E6), and
  redials after any loss with bounded jittered backoff. aio-pika's robust connection is not used:
  the adapter owns reconnecting.
- ``publish`` publishes persistent and mandatory on a confirm channel and returns only when the
  broker acknowledged the message and did not return it; a negative acknowledgment, a return or a
  closed channel raises (aiormq matches a return to its publish by message ID and resolves that
  publish before the acknowledgment, so a return is never mistaken for a confirm). The caller bounds
  the wait with its confirm timeout.
- ``Subscription`` consumes one queue with manual acknowledgment and a bounded prefetch, and
  subscribes again on a new channel, after the backoff, whenever the broker cancels the consumer,
  the channel or connection closes, or ``restart`` is called.

No message body, broker URL or broker error text is ever logged; errors carry their class name.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import aio_pika
from aio_pika.abc import (
    AbstractChannel,
    AbstractConnection,
    AbstractExchange,
    AbstractIncomingMessage,
)
from aio_pika.exceptions import ChannelNotFoundEntity, DeliveryError, PublishError
from pamqp.common import FieldTable

from provisioning.infrastructure.rabbitmq.config import (
    BrokerConfig,
    ExchangeNames,
    ExchangeTopology,
    InvalidBrokerConfig,
    QueueTopology,
)
from provisioning.infrastructure.rabbitmq.headers import field_table
from provisioning.infrastructure.telemetry.tracing import consume_span, inject_headers, publish_span

# Bounds closing a channel or a connection on the way out.
_CLOSE_TIMEOUT_SECONDS = 5.0


class ExchangeMissing(Exception):
    """An exchange checked passively does not exist: a foreign exchange its owner has not
    declared yet, or, with declaration off, an owned one. The connection is refused and redialled
    after the backoff."""


async def _check_exchange(channel: AbstractChannel, name: str) -> None:
    """A passive check of ``name``; ``ExchangeMissing`` when the broker does not know it."""
    try:
        await channel.declare_exchange(name, passive=True)
    except ChannelNotFoundEntity:
        raise ExchangeMissing(f"exchange {name} does not exist") from None


class PublishFailed(Exception):
    """A publish that does not count as published; the dispatcher records it and SQL retries."""


class NotConnected(PublishFailed):
    """No connection or publish channel is open at the moment, or it closed during the publish."""


class PublishNacked(PublishFailed):
    """The broker answered with a negative acknowledgment (a full queue rejects the publish)."""


class PublishReturned(PublishFailed):
    """The broker returned the mandatory message as unroutable."""


def _jittered(base: timedelta, cap: timedelta, attempt: int) -> float:
    """min(cap, base * 2^(attempt-1)) reduced by less than half, in seconds; never zero."""
    delay = base
    for _ in range(1, attempt):
        if delay >= cap:
            break
        delay = cap if delay > cap / 2 else delay * 2
    micros = min(delay, cap) // timedelta(microseconds=1)
    if micros <= 1:
        return 1e-6
    return (micros - secrets.randbelow(micros // 2)) / 1e6


def _table(arguments: dict[str, str | int]) -> FieldTable:
    """Queue arguments as the AMQP field table the library takes."""
    table: FieldTable = {}
    for key, value in arguments.items():
        table[key] = value
    return table


def _setter(event: asyncio.Event) -> Callable[..., None]:
    """A close or cancel callback that sets ``event``."""

    def callback(*_: object) -> None:
        event.set()

    return callback


async def _first_of(*events: asyncio.Event) -> None:
    """Return when any of ``events`` is set."""
    waiters = [asyncio.ensure_future(event.wait()) for event in events]
    try:
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiter in waiters:
            waiter.cancel()


async def _close_quietly(closable: AbstractChannel | AbstractConnection) -> None:
    """Close a channel or connection, bounded, ignoring a failure (it is going away anyway)."""
    with contextlib.suppress(Exception):
        async with asyncio.timeout(_CLOSE_TIMEOUT_SECONDS):
            await closable.close()


class AmqpDelivery:
    """One delivered message from ``queue``; satisfies
    ``presentation.messaging.consumers.messages.Delivery``."""

    __slots__ = ("_message", "_queue")

    def __init__(self, message: AbstractIncomingMessage, queue: str) -> None:
        self._message = message
        self._queue = queue

    @property
    def body(self) -> bytes:
        return self._message.body

    @property
    def content_type(self) -> str | None:
        return self._message.content_type

    @property
    def message_type(self) -> str | None:
        return self._message.type

    @property
    def message_id(self) -> str | None:
        return self._message.message_id

    @property
    def redelivered(self) -> bool:
        return bool(self._message.redelivered)

    def traced(self) -> contextlib.AbstractContextManager[None]:
        """The span ``consume <queue>``, continuing the trace of the ``traceparent`` header (a
        no-op when tracing is off; decision 0022 E9)."""
        return consume_span(self._queue, self._message.headers, message_id=self._message.message_id)

    async def ack(self) -> None:
        await self._message.ack()

    async def dead_letter(self) -> None:
        """Reject without requeue: the queue dead-letters it (counts towards the delivery
        limit)."""
        await self._message.reject(requeue=False)

    async def requeue(self) -> None:
        """Negative acknowledgment with requeue: back to the queue for another delivery."""
        await self._message.nack(requeue=True)


class BrokerClient:
    """One AMQP connection, kept open by ``run``."""

    __slots__ = (
        "_config",
        "_connect_failures",
        "_connection",
        "_connections",
        "_exchanges",
        "_logger",
        "_publish_channel",
        "_ready",
        "_slots",
    )

    def __init__(self, config: BrokerConfig, *, logger: logging.Logger) -> None:
        """Checks the configuration (``InvalidBrokerConfig``); nothing is dialled until
        ``run``."""
        config.validate()
        self._config = config
        self._logger = logger
        self._slots = asyncio.Semaphore(config.max_in_flight_publishes)
        self._ready = asyncio.Event()
        self._connection: AbstractConnection | None = None
        self._publish_channel: AbstractChannel | None = None
        self._exchanges: dict[str, AbstractExchange] = {}
        self._connections = 0
        self._connect_failures = 0

    @property
    def connected(self) -> bool:
        """Whether a connection and its publish channel are open now."""
        return self._ready.is_set()

    @property
    def connections(self) -> int:
        """Connections opened so far, for tests and diagnostics."""
        return self._connections

    @property
    def connect_failures(self) -> int:
        """Failed connection attempts so far, for tests and diagnostics."""
        return self._connect_failures

    async def wait_connected(self) -> None:
        await self._ready.wait()

    async def run(self) -> None:
        """Keep the connection and its publish channel open until cancelled, then close them."""
        attempt = 0
        while True:
            try:
                connection = await self._connect()
            except Exception as exc:  # a dial, login or topology failure: redial after backoff
                attempt += 1
                self._connect_failures += 1
                wait = self._delay(attempt)
                self._logger.warning(
                    "broker connection failed (connection=%s error=%s retry_in=%.3fs)",
                    self._config.connection_name,
                    type(exc).__name__,
                    wait,
                )
                await asyncio.sleep(wait)
                continue
            attempt = 0
            self._connections += 1
            self._logger.info("broker connected (connection=%s)", self._config.connection_name)
            try:
                await self._serve(connection)
            finally:
                self._ready.clear()
                self._publish_channel = None
                self._connection = None
                self._exchanges = {}
                await asyncio.shield(_close_quietly(connection))
            wait = self._delay(1)
            self._logger.warning(
                "broker connection lost (connection=%s retry_in=%.3fs)",
                self._config.connection_name,
                wait,
            )
            await asyncio.sleep(wait)

    def _delay(self, attempt: int) -> float:
        return _jittered(self._config.reconnect_base, self._config.reconnect_max, attempt)

    async def _connect(self) -> AbstractConnection:
        """Dial and prepare the topology on a short-lived channel; any failure closes the
        connection and is raised."""
        dial = self._config.dial_timeout.total_seconds()
        async with asyncio.timeout(dial):
            connection = await aio_pika.connect(
                self._config.url,
                timeout=dial,
                heartbeat=max(1, int(self._config.heartbeat.total_seconds())),
                name=self._config.connection_name,
            )
        try:
            async with asyncio.timeout(dial):
                channel = await connection.channel(publisher_confirms=False)
                try:
                    await self._prepare(channel)
                finally:
                    await _close_quietly(channel)
        except BaseException:
            await asyncio.shield(_close_quietly(connection))
            raise
        return connection

    def _exchanges_used(self) -> dict[str, tuple[ExchangeNames, ExchangeTopology | None]]:
        """Every exchange the process uses, by name, first mention first: each queue's exchange
        and dead-letter exchange (direct, owned), then the publish-only exchanges, which carry
        the limits of an alternate exchange's queue."""
        found: dict[str, tuple[ExchangeNames, ExchangeTopology | None]] = {}
        for topology in self._config.topologies:
            names = topology.names
            dlx = ExchangeNames(names.dead_letter_exchange, "direct", True)
            for exchange in (names.exchange_names, dlx):
                found.setdefault(exchange.name, (exchange, None))
        for publish in self._config.publish_exchanges:
            known = found.get(publish.names.name)
            if known is None or known[1] is None:
                found[publish.names.name] = (publish.names, publish)
        return found

    async def _prepare(self, channel: AbstractChannel) -> None:
        """Check the foreign exchanges passively first (``ExchangeMissing``, nothing declared),
        then declare (or, with declaration off, check passively) the owned exchanges, an owned
        topic exchange's alternate exchange and its queue, the queues and the bindings. A
        mismatch closes the channel and raises."""
        passive = not self._config.declare_topology
        exchanges = self._exchanges_used()
        for names, _ in exchanges.values():
            if not names.owned:
                await _check_exchange(channel, names.name)
        for names, publish in exchanges.values():
            if not names.owned:
                continue
            if passive:
                await _check_exchange(channel, names.name)
                if names.alternate_exchange is not None:
                    await _check_exchange(channel, names.alternate_exchange)
                    await channel.declare_queue(names.alternate_exchange, passive=True)
                continue
            arguments: dict[str, str | int] = {}
            alternate = names.alternate_exchange
            if alternate is not None:
                if publish is None:
                    raise InvalidBrokerConfig("an owned topic exchange needs its publish limits")
                unrouted = await channel.declare_exchange(
                    alternate, aio_pika.ExchangeType.FANOUT, durable=True
                )
                kept = await channel.declare_queue(
                    alternate, durable=True, arguments=_table(publish.unrouted_arguments())
                )
                await kept.bind(unrouted, "")
                arguments["alternate-exchange"] = alternate
            await channel.declare_exchange(
                names.name,
                aio_pika.ExchangeType(names.exchange_type),
                durable=True,
                arguments=_table(arguments) if arguments else None,
            )
        for topology in self._config.topologies:
            await _prepare_queues(channel, topology, passive)

    async def _serve(self, connection: AbstractConnection) -> None:
        """Keep a confirm channel open on ``connection`` until it closes."""
        connection_closed = asyncio.Event()
        connection.close_callbacks.add(_setter(connection_closed))
        self._connection = connection
        attempt = 0
        while not connection_closed.is_set() and not connection.is_closed:
            try:
                channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
            except Exception as exc:  # the connection may be going; checked by the loop
                attempt += 1
                wait = self._delay(attempt)
                self._logger.warning(
                    "opening the publish channel failed (error=%s retry_in=%.3fs)",
                    type(exc).__name__,
                    wait,
                )
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(wait):
                        await connection_closed.wait()
                continue
            attempt = 0
            channel_closed = asyncio.Event()
            channel.close_callbacks.add(_setter(channel_closed))
            self._exchanges = {}
            self._publish_channel = channel
            self._ready.set()
            try:
                await _first_of(connection_closed, channel_closed)
            finally:
                self._ready.clear()
                self._publish_channel = None
            if not connection_closed.is_set():
                self._logger.warning("publish channel closed; opening another")
                await _close_quietly(channel)

    async def _wait_connection(self) -> AbstractConnection:
        """The open connection, waiting until ``run`` provides one."""
        while True:
            await self._ready.wait()
            connection = self._connection
            if connection is not None and not connection.is_closed:
                return connection
            await asyncio.sleep(self._config.reconnect_base.total_seconds())

    async def publish(
        self,
        *,
        exchange: str,
        routing_key: str,
        body: bytes,
        content_type: str,
        message_type: str,
        message_id: str,
        correlation_id: str,
        timestamp: datetime,
    ) -> None:
        """Publish persistent and mandatory with a confirm. Raises ``NotConnected``,
        ``PublishNacked`` or ``PublishReturned``; the caller bounds the wait."""
        if not self._ready.is_set():
            raise NotConnected("broker not connected")
        async with self._slots:
            channel = self._publish_channel
            if channel is None or channel.is_closed:
                raise NotConnected("broker not connected")
            target = self._exchanges.get(exchange)
            if target is None:
                target = await channel.get_exchange(exchange, ensure=False)
                self._exchanges[exchange] = target
            # The publish span; its traceparent goes into the headers when tracing is on.
            with publish_span(exchange, routing_key, message_id=message_id):
                headers: dict[str, object] = {}
                inject_headers(headers)
                message = aio_pika.Message(
                    body,
                    content_type=content_type,
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    message_id=message_id,
                    correlation_id=correlation_id,
                    type=message_type,
                    timestamp=timestamp,
                    app_id="provisioning",
                    headers=field_table(headers) if headers else None,
                )
                try:
                    await target.publish(message, routing_key, mandatory=True)
                except PublishError:
                    raise PublishReturned("publish returned as unroutable") from None
                except DeliveryError:
                    raise PublishNacked("publish not confirmed by the broker") from None
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # a closed channel or connection during the publish
                    raise NotConnected(
                        f"publish failed on a closing channel ({type(exc).__name__})"
                    ) from None

    def subscribe(self, queue: str, *, prefetch: int) -> Subscription:
        """A subscription to ``queue``; it consumes while its ``run`` runs."""
        if not 1 <= prefetch <= 65535:
            raise ValueError("prefetch must be 1 to 65535")
        return Subscription(
            queue,
            prefetch,
            wait_connection=self._wait_connection,
            delay=self._delay,
            logger=self._logger,
        )


async def _prepare_queues(channel: AbstractChannel, topology: QueueTopology, passive: bool) -> None:
    names = topology.names
    if passive:
        await channel.declare_queue(names.dead_letter_queue, passive=True)
        await channel.declare_queue(names.queue, passive=True)
        return
    dead = await channel.declare_queue(
        names.dead_letter_queue,
        durable=True,
        arguments=_table(topology.dead_letter_arguments()),
    )
    queue = await channel.declare_queue(
        names.queue, durable=True, arguments=_table(topology.queue_arguments())
    )
    for key in names.routing_keys:
        await dead.bind(names.dead_letter_exchange, key)
        await queue.bind(names.exchange, key)


class Subscription:
    """Consumes one queue with manual acknowledgment and a bounded prefetch; satisfies
    ``presentation.messaging.consumers.messages.DeliveryStream``."""

    __slots__ = (
        "_attempts",
        "_buffer",
        "_delay",
        "_logger",
        "_prefetch",
        "_queue",
        "_restart",
        "_stopped",
        "_subscriptions",
        "_wait_connection",
    )

    def __init__(
        self,
        queue: str,
        prefetch: int,
        *,
        wait_connection: Callable[[], Awaitable[AbstractConnection]],
        delay: Callable[[int], float],
        logger: logging.Logger,
    ) -> None:
        """``wait_connection`` waits for the client's open connection; ``delay`` is the
        client's backoff for an attempt number."""
        self._wait_connection = wait_connection
        self._delay = delay
        self._logger = logger
        self._queue = queue
        self._prefetch = prefetch
        # The broker holds back more than ``prefetch`` unsettled deliveries per channel, so the
        # buffer stays small; a channel being replaced may add one more prefetch.
        self._buffer: asyncio.Queue[AmqpDelivery] = asyncio.Queue(maxsize=2 * prefetch)
        self._restart = asyncio.Event()
        self._stopped = asyncio.Event()
        self._subscriptions = 0
        self._attempts = 0

    @property
    def subscriptions(self) -> int:
        """How many times a consumer was registered with the broker."""
        return self._subscriptions

    def restart(self) -> None:
        """Close the current consumer's channel, so that its unsettled deliveries return to the
        queue, and subscribe again after the backoff."""
        self._restart.set()

    async def next_delivery(self) -> AmqpDelivery | None:
        """The next delivery, or None once ``run`` has stopped and the buffer is empty."""
        if self._stopped.is_set() and self._buffer.empty():
            return None
        getting = asyncio.ensure_future(self._buffer.get())
        stopping = asyncio.ensure_future(self._stopped.wait())
        try:
            await asyncio.wait({getting, stopping}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stopping.cancel()
            if not getting.done():
                getting.cancel()
        if getting.done() and not getting.cancelled():
            return getting.result()
        return None

    async def run(self) -> None:
        """Consume until cancelled, subscribing again after every end."""
        log = self._logger
        try:
            while True:
                reason = await self._consume_once()
                self._attempts = min(self._attempts + 1, 64)
                wait = self._delay(self._attempts)
                log.warning(
                    "consumer ended; subscribing again (queue=%s reason=%s retry_in=%.3fs)",
                    self._queue,
                    reason,
                    wait,
                )
                await asyncio.sleep(wait)
        finally:
            self._stopped.set()

    async def _consume_once(self) -> str:
        connection = await self._wait_connection()
        try:
            channel = await connection.channel(publisher_confirms=False)
        except Exception as exc:  # the connection is going; run waits for the next one
            return f"open channel failed ({type(exc).__name__})"
        try:
            ended = asyncio.Event()
            channel.close_callbacks.add(_setter(ended))
            underlay = await channel.get_underlay_channel()
            underlay.on_consumer_cancel_callbacks.add(_setter(ended))
            await channel.set_qos(prefetch_count=self._prefetch)
            queue = await channel.get_queue(self._queue, ensure=False)
            # A restart asked for while no consumer was registered is satisfied by this one.
            self._restart.clear()
            await queue.consume(self._on_message, no_ack=False)
            self._subscriptions += 1
            await _first_of(ended, self._restart)
            if self._restart.is_set():
                return "restart requested"
            return "consumer cancelled or channel closed"
        except Exception as exc:  # the queue is missing or the channel closed while setting up
            return f"subscribe failed ({type(exc).__name__})"
        finally:
            await asyncio.shield(_close_quietly(channel))

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        self._attempts = 0
        await self._buffer.put(AmqpDelivery(message, self._queue))
