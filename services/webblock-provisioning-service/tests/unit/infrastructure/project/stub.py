"""A local stand-in for the Project service's access-check endpoint, for the adapter's tests (task
PA; decision 0017 section 2): a plain HTTP/1.1 server on 127.0.0.1 that records every request
whole and answers what the test sets. Nothing here is imported by the service; not a test module.

The answers follow the Project service's real contract (its ``AccessCheckControllerTest`` and the
standard envelope ``{"success", "data", "error", "meta"}``): an allowed check has no reason, a
denial is a ``200`` carrying its reason, everything else is an error envelope with another status.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

META: dict[str, Any] = {"requestId": "stub", "timestamp": "2026-10-14T12:00:00Z"}


def envelope(data: Any, *, success: bool = True, error: Any = None) -> bytes:
    """The Project service's response envelope around ``data``."""
    return json.dumps({"success": success, "data": data, "error": error, "meta": META}).encode()


def allowed() -> bytes:
    return envelope({"allowed": True})


def denied(reason: str) -> bytes:
    return envelope({"allowed": False, "reason": reason})


@dataclass(frozen=True)
class Recorded:
    """One request as the server read it: the method, the target, the headers (names in lower
    case) and the body."""

    method: str
    target: str
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


@dataclass
class AccessCheckServer:
    """``status``, ``body`` and ``headers`` are the answer. ``delay`` holds the answer back,
    ``hang_up`` closes the connection without one, ``chunked`` sends the body without a length,
    ``location`` adds a Location header (for a redirect), and ``drip`` (seconds) sends the body 8
    bytes at a time with that pause before each piece, so every read arrives in time but the whole
    answer is slow."""

    status: int = 200
    body: bytes = field(default_factory=allowed)
    headers: dict[str, str] = field(default_factory=lambda: {"Content-Type": "application/json"})
    delay: float = 0.0
    hang_up: bool = False
    chunked: bool = False
    location: str | None = None
    drip: float = 0.0
    requests: list[Recorded] = field(default_factory=list)
    port: int = 0
    _server: asyncio.Server | None = field(default=None, repr=False)

    def url(self, path: str = "") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    async def _read(self, reader: asyncio.StreamReader) -> Recorded:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        lines = head.decode("latin-1").split("\r\n")
        method, target, _ = lines[0].split(" ", 2)
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if line:
                name, _, value = line.partition(":")
                headers[name.strip().lower()] = value.strip()
        length = int(headers.get("content-length", "0"))
        body = await asyncio.wait_for(reader.readexactly(length), timeout=5) if length else b""
        return Recorded(method=method, target=target, headers=headers, body=body)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            self.requests.append(await self._read(reader))
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.hang_up:
                return
            extra = "".join(f"{k}: {v}\r\n" for k, v in self.headers.items())
            if self.location is not None:
                extra += f"Location: {self.location}\r\n"
            if self.chunked:
                head = f"HTTP/1.1 {self.status} X\r\n{extra}Transfer-Encoding: chunked\r\n"
                writer.write(f"{head}Connection: close\r\n\r\n".encode())
                for start in range(0, len(self.body), 4096):
                    piece = self.body[start : start + 4096]
                    writer.write(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
                writer.write(b"0\r\n\r\n")
            elif self.drip:
                head = f"HTTP/1.1 {self.status} X\r\n{extra}Content-Length: {len(self.body)}\r\n"
                writer.write(f"{head}Connection: close\r\n\r\n".encode())
                await writer.drain()
                for start in range(0, len(self.body), 8):
                    await asyncio.sleep(self.drip)
                    writer.write(self.body[start : start + 8])
                    await writer.drain()
            else:
                head = f"HTTP/1.1 {self.status} X\r\n{extra}Content-Length: {len(self.body)}\r\n"
                writer.write(f"{head}Connection: close\r\n\r\n".encode() + self.body)
            await writer.drain()
        except ConnectionError, asyncio.IncompleteReadError, TimeoutError:
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server.close_clients()
            await self._server.wait_closed()
            self._server = None


@contextlib.asynccontextmanager
async def access_check_server() -> AsyncIterator[AccessCheckServer]:
    server = AccessCheckServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


async def closed_port() -> int:
    """A port on 127.0.0.1 that nothing listens on (bound, then released)."""
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port: int = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    return port
