"""The operator CLI (decision 0014 section 21 ruling 11; .claude/docs/runbooks/operator.md).

    python -m provisioning.hosts.operator list [--limit N]
    python -m provisioning.hosts.operator redrive --project P --generation-request G --by NAME \\
        [--extra-attempts N]
    python -m provisioning.hosts.operator subdomain missing [--limit N]
    python -m provisioning.hosts.operator subdomain set --project P --subdomain S --by NAME
    python -m provisioning.hosts.operator dlq list [--queue Q] [--limit N]
    python -m provisioning.hosts.operator dlq redrive --queue Q --message-id ID --by NAME \\
        [--scan-limit N]

It connects as a login that is a member of ``provisioning_operator`` alone (PG_USER, PG_PASS) with
the server settings and transaction bounds of every other process (PG_*, DB_*, LOG_LEVEL). The
``dlq`` commands (task W6-E2) read only the broker section instead: RABBITMQ_URL,
RABBITMQ_DIAL_TIMEOUT, DLQ_CALL_TIMEOUT and LOG_LEVEL, and open no database connection. No command
holds a cluster credential. The command's result goes to standard output; the reason of a
refusal, the JSON log lines and the audit events (``audit=true``) go to standard error. With a
missing or invalid setting it names every refused key, never a value, and exits 2; a usage error
exits 2; a refused or failed re-drive or sub-domain set exits 1.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping
from typing import Final

from provisioning.hosts.operator.cli import EXIT_REFUSED, execute, parse_args
from provisioning.hosts.operator.dlq_cli import execute_dlq
from provisioning.hosts.shared.runtime import (
    EXIT_CONFIG,
    InvalidSettings,
    SystemClock,
    open_role_engine,
    report_config,
    report_settings,
    route_standard_logging,
    timeouts_of,
)
from provisioning.infrastructure.config import LoadError, OperatorConfig, load_operator
from provisioning.infrastructure.config.operator import (
    OperatorBrokerConfig,
    load_operator_broker,
)
from provisioning.infrastructure.postgres.operator_store import PgOperatorStore
from provisioning.infrastructure.rabbitmq.dlq import (
    PLATFORM_DEAD_LETTER_TOPOLOGIES,
    DeadLetterTool,
    amqp_sessions,
)
from provisioning.infrastructure.telemetry import LoggerAuditLog, new_logger

PROCESS_LABEL: Final = "provisioning-operator"


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    args = parse_args(argv)
    env = os.environ if environ is None else environ
    if args.command == "dlq":
        return _main_dlq(args, env)
    try:
        config = load_operator(env.get)
    except LoadError as err:
        report_config(sys.stderr, PROCESS_LABEL, err)
        return EXIT_CONFIG
    route_standard_logging(sys.stderr, config.log_level)
    try:
        return asyncio.run(_run(config, args))
    except InvalidSettings as err:
        report_settings(sys.stderr, PROCESS_LABEL, err)
        return EXIT_CONFIG


def _main_dlq(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    try:
        config = load_operator_broker(env.get)
    except LoadError as err:
        report_config(sys.stderr, PROCESS_LABEL, err)
        return EXIT_CONFIG
    route_standard_logging(sys.stderr, config.log_level)
    return asyncio.run(_run_dlq(config, args))


async def _run_dlq(config: OperatorBrokerConfig, args: argparse.Namespace) -> int:
    logger = new_logger(sys.stderr, config.log_level)
    tool = DeadLetterTool(
        PLATFORM_DEAD_LETTER_TOPOLOGIES,
        amqp_sessions(
            config.url.reveal(),
            dial_timeout=config.dial_timeout,
            call_timeout=config.call_timeout,
            connection_name=PROCESS_LABEL,
        ),
    )
    try:
        return await execute_dlq(
            args,
            tool=tool,
            audit=LoggerAuditLog(logger),
            clock=SystemClock(),
            out=sys.stdout,
            err=sys.stderr,
        )
    except Exception as exc:  # the tool's own errors carry no body, URL or broker text
        logger.error("operator command failed", error=type(exc).__name__)
        return EXIT_REFUSED


async def _run(config: OperatorConfig, args: argparse.Namespace) -> int:
    logger = new_logger(sys.stderr, config.log_level)
    engine = open_role_engine(config.database, config.operator_login)
    try:
        store = PgOperatorStore(engine, timeouts_of(config.database))
        try:
            return await execute(
                args,
                store=store,
                audit=LoggerAuditLog(logger),
                clock=SystemClock(),
                out=sys.stdout,
                err=sys.stderr,
            )
        except InvalidSettings:
            raise
        except Exception as exc:  # the store's own errors carry no driver text or value
            logger.error("operator command failed", error=type(exc).__name__)
            return EXIT_REFUSED
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
