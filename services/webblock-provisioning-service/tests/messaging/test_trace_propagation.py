"""Trace propagation over a real broker (decision 0022 E9): a command job published inside a span
carries its ``traceparent``, and the consumer span continues that trace; with tracing off nothing
is added."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from tests.messaging._broker import Broker, broker_config, limits, logger, running, unique_names

from provisioning.core.application import WorkRef
from provisioning.core.domain import CommandKind
from provisioning.infrastructure.rabbitmq import BrokerClient, QueueTopology
from provisioning.infrastructure.telemetry.tracing import (
    configure_tracing,
    current_trace_id,
    shutdown_tracing,
    span,
)
from provisioning.presentation.messaging import CommandJobCodec, CommandJobPublisher
from provisioning.presentation.messaging.consumers.messages import Action, Delivery, serve, settle

pytestmark = pytest.mark.asyncio(loop_scope="function")

AT = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
PROJECT = UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e01")
RELEASE = UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e02")
COMMAND = UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e03")
MESSAGE = UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e04")


@pytest.fixture(autouse=True)
def _tracing_off_after() -> Iterator[None]:
    yield
    shutdown_tracing()


def _enable(exporter: InMemorySpanExporter | None = None) -> InMemorySpanExporter:
    exporter = exporter if exporter is not None else InMemorySpanExporter()
    configure_tracing(
        service_name="provisioning-test",
        endpoint="http://127.0.0.1:9",
        ratio=1.0,
        exporter=exporter,
    )
    return exporter


async def _publish_and_consume(
    broker: Broker, *, inside: str | None, trace_from_consume: InMemorySpanExporter | None = None
) -> str | None:
    """Publish one command job (inside a span named ``inside`` when given), consume it through
    ``serve`` and return the trace ID current while it was handled. With ``trace_from_consume``
    tracing is turned on only after the publish, exporting to it."""
    names = unique_names("trace")
    topology = QueueTopology(names=names, limits=limits())
    client = BrokerClient(broker_config(broker.url, (topology,)), logger=logger("trace"))
    publisher = CommandJobPublisher(
        codec=CommandJobCodec(max_body_bytes=4096),
        raw=client,
        exchange=names.exchange,
        routing_key=names.routing_keys[0],
    )
    ref = WorkRef(
        command_id=COMMAND,
        project_id=PROJECT,
        kind=CommandKind.DEPLOY,
        command_number=1,
        release_id=RELEASE,
    )
    seen: list[str | None] = []
    async with running(client):
        with span(inside, kind=SpanKind.SERVER) if inside else contextlib.nullcontext():
            await publisher.publish_command_job(
                message_id=MESSAGE, ref=ref, correlation_id="c-1", occurred_at=AT
            )
        if trace_from_consume is not None:
            _enable(trace_from_consume)
        subscription = client.subscribe(names.queue, prefetch=1)
        consuming = asyncio.create_task(subscription.run())

        async def handle(delivery: Delivery) -> bool:
            seen.append(current_trace_id())
            await settle(delivery, Action.ACK, logging.getLogger("t"), "k=v")
            consuming.cancel()
            return False

        await asyncio.wait_for(serve(subscription, 1, handle), timeout=20)
        with pytest.raises(asyncio.CancelledError):
            await consuming
    (trace_id,) = seen
    return trace_id


async def test_a_command_job_published_in_a_span_is_consumed_in_the_same_trace(
    broker: Broker,
) -> None:
    """FR-43.1: a command job published in a span is consumed in the same trace."""
    exporter = _enable()
    trace_id = await _publish_and_consume(broker, inside="POST /v1/projects/{project_id}")
    spans = {s.name: s for s in exporter.get_finished_spans()}
    request = spans["POST /v1/projects/{project_id}"]
    publish = next(s for name, s in spans.items() if name.startswith("publish "))
    consume = next(s for name, s in spans.items() if name.startswith("consume "))
    assert request.context is not None and publish.context is not None
    assert consume.context is not None
    assert publish.kind is SpanKind.PRODUCER and consume.kind is SpanKind.CONSUMER
    assert publish.context.trace_id == request.context.trace_id
    assert consume.context.trace_id == request.context.trace_id
    # The consumer's parent is the publish span: only the traceparent header carries it.
    assert consume.parent is not None and consume.parent.span_id == publish.context.span_id
    assert trace_id == f"{request.context.trace_id:032x}"
    assert consume.attributes is not None
    assert consume.attributes["messaging.message.id"] == str(MESSAGE)


async def test_a_job_published_with_tracing_off_carries_no_traceparent(broker: Broker) -> None:
    exporter = InMemorySpanExporter()
    trace_id = await _publish_and_consume(broker, inside=None, trace_from_consume=exporter)
    (consume,) = exporter.get_finished_spans()
    assert consume.name.startswith("consume ")
    assert consume.parent is None  # no header: the consumer span starts a new trace
    assert consume.context is not None and trace_id == f"{consume.context.trace_id:032x}"
