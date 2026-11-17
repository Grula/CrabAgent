"""URL allow/deny/blocklist policy for the collaboration browser.

The platform-neutral bridge enforces these rules:

* only ``http`` and ``https`` URLs can be opened;
* credentials embedded in URLs are rejected;
* loopback, private, link-local, CGNAT, benchmark and reserved addresses are
  blocked (including the AWS metadata address ``169.254.169.254``);
* DNS rebinding is blocked by validating every resolved address;
* local previews are limited to the exact loopback hosts ``127.0.0.1``,
  ``[::1]`` and ``localhost``, granted for a bounded TTL, and CrabAgent
  service ports can never be authorized.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
from typing import Callable
from urllib.parse import SplitResult, urlsplit

_SCHEME_RE = re.compile(r"^[a-z][a-z\d+.-]*:", re.IGNORECASE)


def blocked_ipv4(address: str) -> bool:
    """Return True when ``address`` is not a routable public IPv4 address."""
    parts = address.split(".")
    if len(parts) != 4:
        return True
    nums: list[int] = []
    for part in parts:
        if not part.isdigit():
            return True
        nums.append(int(part))
    if any(n < 0 or n > 255 for n in nums):
        return True
    a, b = nums[0], nums[1]
    if a == 0 or a == 10 or a == 127:
        return True  # this-host, private, loopback
    if a == 100 and 64 <= b <= 127:
        return True  # CGNAT
    if a == 169 and b == 254:
        return True  # link-local (includes 169.254.169.254 metadata)
    if a == 172 and 16 <= b <= 31:
        return True  # private
    if a == 192 and b == 0 and nums[2] == 0:
        return True  # TEST-NET-1
    if a == 192 and b == 168:
        return True  # private
    if a == 198 and (b == 18 or b == 19):
        return True  # benchmarking (fake-ip VPNs)
    if a >= 224:
        return True  # multicast + reserved
    return False


def blocked_ipv6(address: str) -> bool:
    """Return True when ``address`` is not a routable public IPv6 address."""
    host = address.lower()
    if host in ("::", "::1"):
        return True  # unspecified, loopback
    if host.startswith("::ffff:"):
        return True  # IPv4-mapped (validated as IPv4 separately)
    if host.startswith(("fc", "fd")):
        return True  # unique local fc00::/7
    if host.startswith(("fe8", "fe9", "fea", "feb")):
        return True  # link-local fe80::/10
    if host.startswith(("fe", "ff")):
        return True  # multicast/reserved ff00::/8 and fallback
    if host.startswith("2001:db8:"):
        return True  # documentation
    return False


def parse_browser_url(raw: str) -> SplitResult:
    """Parse a user-supplied URL into an ``http``/``https`` ``SplitResult``.

    A bare host is treated as ``https://<host>`` and only http/https are
    accepted.
    """
    value = str(raw or "").strip()
    parsed = urlsplit(value if _SCHEME_RE.match(value) else f"https://{value}")
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http and https websites can be opened here.")
    if parsed.username or parsed.password:
        raise ValueError("Credentials in URLs are not allowed.")
    if not parsed.hostname:
        raise ValueError("Invalid URL")
    return parsed


def normalize_browser_url(raw: str) -> str:
    return parse_browser_url(raw).geturl()


def blocked_host(hostname: str) -> bool:
    """Return True for localhost/private/loopback/reserved hosts or addresses."""
    host = hostname.strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local") or host.endswith(".internal"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.version == 4:
        return blocked_ipv4(str(ip))
    return blocked_ipv6(str(ip))


def _canonical_origin(parsed: SplitResult) -> str:
    """Reconstruct the URL origin exactly like the WHATWG ``URL.origin``."""
    host = (parsed.hostname or "").lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    default = 443 if parsed.scheme == "https" else 80
    if port and port != default:
        return f"{parsed.scheme}://{host}:{port}"
    return f"{parsed.scheme}://{host}"


def local_preview_origin(raw: str) -> str:
    """Return the canonical origin, restricted to exact loopback hosts."""
    parsed = parse_browser_url(raw)
    hostname = (parsed.hostname or "").lower()
    if hostname not in ("127.0.0.1", "::1", "[::1]", "localhost"):
        raise ValueError("LOCAL_PREVIEW_ONLY: use 127.0.0.1, [::1] or localhost")
    return _canonical_origin(parsed)


class LocalPreviewPolicy:
    """Bounded TTL grants for loopback preview origins."""

    def __init__(
        self,
        ttl_ms: float = 30 * 60_000,
        now: Callable[[], float] = time.monotonic,
        forbidden_ports: Callable[[], list[int]] | None = None,
        allow_all_loopback: bool = False,
    ) -> None:
        self._ttl_seconds = ttl_ms / 1000.0
        self._now = now
        self._forbidden_ports = forbidden_ports or (lambda: [])
        self._allow_all_loopback = allow_all_loopback
        self._grants: dict[tuple[str, str, int | None], float] = {}

    def origin(self, raw: str) -> str:
        value = local_preview_origin(raw)
        parsed = urlsplit(value)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port in self._forbidden_ports():
            raise ValueError("LOCAL_PREVIEW_RESERVED: CrabAgent service ports cannot be authorized")
        return value

    def _key(self, raw: str) -> tuple[str, str, int]:
        parsed = urlsplit(self.origin(raw))
        return (parsed.scheme, (parsed.hostname or "").lower(), parsed.port or (443 if parsed.scheme == "https" else 80))

    def grant(self, raw: str, *, all_ports: bool = False) -> str:
        value = self.origin(raw)
        scheme, host, port = self._key(raw)
        self._grants[(scheme, host, None if all_ports else port)] = self._now() + self._ttl_seconds
        return value

    def allows(self, raw: str) -> bool:
        try:
            scheme, host, port = self._key(raw)
        except Exception:
            return False
        if self._allow_all_loopback:
            return True
        now = self._now()
        wildcard = (self._grants.get((scheme, host, None)) or 0) > now and port not in self._forbidden_ports()
        if (self._grants.get((scheme, host, port)) or 0) > now or wildcard:
            return True
        self._grants.pop((scheme, host, port), None)
        return False

    def clear(self) -> None:
        self._grants.clear()

    def list(self) -> list[str]:
        values: list[str] = []
        for (scheme, host, port), expires in list(self._grants.items()):
            if expires <= self._now():
                self._grants.pop((scheme, host, port), None)
                continue
            name = f"[{host}]" if ":" in host else host
            if port is None:
                values.append(f"{scheme}://{name}:*")
            elif port == (443 if scheme == "https" else 80):
                values.append(f"{scheme}://{name}")
            else:
                values.append(f"{scheme}://{name}:{port}")
        return values


async def allowed_network_url(raw: str, local_preview_policy: LocalPreviewPolicy | None = None) -> str:
    """Validate a URL and its resolved addresses before allowing navigation."""
    parsed = parse_browser_url(raw)
    url = parsed.geturl()
    if local_preview_policy is not None and local_preview_policy.allows(url):
        return url
    if blocked_host(parsed.hostname or ""):
        raise ValueError("PRIVATE_NETWORK_BLOCKED: local or private network address")
    records = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname or "", None)
    if not records or any(blocked_host(record[4][0]) for record in records):
        raise ValueError("PRIVATE_NETWORK_BLOCKED: resolved to a local or private network address")
    return url


__all__ = [
    "blocked_ipv4",
    "blocked_ipv6",
    "parse_browser_url",
    "normalize_browser_url",
    "blocked_host",
    "local_preview_origin",
    "LocalPreviewPolicy",
    "allowed_network_url",
]
