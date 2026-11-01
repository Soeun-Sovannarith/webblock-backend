"""Network value checks shared by the sections: hosts, listen addresses, origins and URLs."""

from __future__ import annotations

import ipaddress
import re
from typing import Final
from urllib.parse import SplitResult, urlsplit

from provisioning.infrastructure.config.loader import has_control

_HOST_CHARS: Final = re.compile(r"[A-Za-z0-9._-]+", re.ASCII)
_PORT: Final = re.compile(r"[0-9]{1,5}", re.ASCII)


def valid_host(host: str) -> bool:
    """An IP address, or a host name of letters, digits, '-', '.' and '_' (at most 253)."""
    if _is_ip(host):
        return True
    return 0 < len(host) <= 253 and _HOST_CHARS.fullmatch(host) is not None


def split_host_port(value: str) -> tuple[str, str] | None:
    """Go's net.SplitHostPort: "host:port", "[v6]:port" or ":port"; None when malformed."""
    if value.startswith("["):
        end = value.find("]")
        if end < 0 or value[end + 1 : end + 2] != ":":
            return None
        host, port = value[1:end], value[end + 2 :]
        if "[" in host or "]" in port or "[" in port:
            return None
        return host, port
    host, sep, port = value.rpartition(":")
    if not sep or ":" in host or "[" in host or "]" in host:
        return None
    return host, port


def valid_port(port: str) -> bool:
    """A decimal port from 1 to 65535 without a sign."""
    return _PORT.fullmatch(port) is not None and 1 <= int(port) <= 65535


def listen_addr(value: str) -> str | None:
    """host:port or :port, with a numeric port from 1 to 65535 and an optional host."""
    parts = split_host_port(value)
    if parts is None:
        return "must be host:port or :port"
    host, port = parts
    if not valid_port(port):
        return "must have a numeric port from 1 to 65535"
    if host and not valid_host(host):
        return "must have an IP address or a host name"
    return None


def listeners_collide(a: str, b: str) -> bool:
    """Whether two checked listen addresses cannot both be bound: the same port with the same
    host, or with a wildcard host on either side."""
    pa, pb = split_host_port(a), split_host_port(b)
    if pa is None or pb is None or pa[1] != pb[1]:
        return False
    host_a, host_b = pa[0], pb[0]
    if _is_wildcard(host_a) or _is_wildcard(host_b):
        return True
    ip_a, ip_b = _ip(host_a), _ip(host_b)
    if ip_a is not None and ip_b is not None:
        return _unmap(ip_a) == _unmap(ip_b)
    return host_a.lower() == host_b.lower()


def split_url(value: str) -> SplitResult | None:
    """``value``'s URL parts, or None when it does not parse or its port is not a valid number;
    the port is read only to refuse an invalid one."""
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    del port
    return parts


def valid_origin(value: str) -> bool:
    """An origin of the form http(s)://host[:port], without credentials, path, query or
    fragment."""
    if has_control(value) or "#" in value or "?" in value:
        return False
    parts = split_url(value)
    if parts is None:
        return False
    return (
        parts.scheme in ("http", "https")
        and bool(parts.hostname)
        and "@" not in parts.netloc
        and parts.path == ""
        and value == f"{parts.scheme}://{parts.netloc}"
    )


def valid_allowed_host(value: str) -> bool:
    """A Host header pattern for the MCP endpoint: host, host:port or host:* (the SDK's wildcard
    port), with an IPv6 address in brackets."""
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            return False
        host, rest = value[1:end], value[end + 1 :]
        if not _is_ip(host) or ":" not in host:
            return False
    else:
        host, sep, port = value.partition(":")
        rest = sep + port
        if not valid_host(host) or ":" in port:
            return False
    if rest == "" or rest == ":*":
        return True
    return rest.startswith(":") and valid_port(rest[1:])


def http_url(value: str, *, schemes: tuple[str, ...]) -> bool:
    """An absolute URL with one of ``schemes`` and a host, without credentials, query or
    fragment, at most 2048 bytes. Used for URLs that may otherwise carry credentials, so callers
    give a reason that never repeats the value."""
    if len(value) > 2048 or has_control(value) or "#" in value or "?" in value:
        return False
    parts = split_url(value)
    if parts is None:
        return False
    return (
        parts.scheme.lower() in schemes
        and bool(parts.hostname)
        and "@" not in parts.netloc
        and parts.query == ""
        and parts.fragment == ""
    )


def _ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _is_ip(host: str) -> bool:
    return _ip(host) is not None


def _is_wildcard(host: str) -> bool:
    if host == "":
        return True
    ip = _ip(host)
    return ip is not None and ip.is_unspecified


def _unmap(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped
        if mapped is not None:
            return mapped
    return ip
