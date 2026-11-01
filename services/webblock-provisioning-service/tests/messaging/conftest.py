"""The real-RabbitMQ suite (decision 0005 section 8, decision 0014 section 4).

One disposable ``rabbitmq:4.3.6-management-alpine`` container per session, started through
Testcontainers; the owner's ``rabbitmq`` container is never used and ``.env`` is never read.
Every test declares its own uniquely named exchanges and queues. Fault tests stop and start the
broker application inside this container only (``rabbitmqctl`` as the ``rabbitmq`` user, decision
0005 section 8). A missing Docker or a broker that never accepts connections fails the run.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from tests.messaging._broker import Broker, start_broker


@pytest.fixture(scope="session")
def broker() -> Generator[Broker]:
    container, running = start_broker()
    try:
        yield running
    finally:
        container.stop()
