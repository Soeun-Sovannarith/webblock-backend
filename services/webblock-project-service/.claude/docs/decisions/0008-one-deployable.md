# 0008 One deployable

## Decision

The Project service is one deployable: the API, the listeners, the outbox relay and the durable jobs run in the same
process. Split it only if measured latency requires it.

## Reason

One process is simpler to build, deploy and test. The outbox and the jobs scale with the service's instances.

## Status

Accepted.
