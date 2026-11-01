"""Graceful shutdown (decision 0006): the first SIGINT or SIGTERM asks the process to stop; a second
one ends it at once, by the signal's default action."""

from __future__ import annotations

import signal
import subprocess
import sys
import textwrap

SCRIPT = textwrap.dedent(
    """
    import asyncio, os, signal, sys
    from provisioning.hosts.api.signals import install_stop_signals

    async def main() -> None:
        stop = asyncio.Event()
        install_stop_signals(asyncio.get_running_loop(), stop)
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(stop.wait(), timeout=10)
        print("stopping", flush=True)
        if sys.argv[1] == "twice":
            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.sleep(10)
        print("survived", flush=True)

    asyncio.run(main())
    """
)


def _run(mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # fixed argument list: the test interpreter and a script
        [sys.executable, "-c", SCRIPT, mode],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_the_first_signal_asks_the_process_to_stop() -> None:
    result = _run("once")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["stopping", "survived"]


def test_a_second_signal_ends_the_process_at_once() -> None:
    result = _run("twice")
    assert result.returncode == -signal.SIGTERM, result.stderr
    assert result.stdout.splitlines() == ["stopping"]
