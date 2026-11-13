"""Let long-lived responses (event streams) end on their own when the server stops.

On Ctrl+C uvicorn stops accepting connections and then waits for open ones to
finish. Browser event streams never finish by themselves, so without this the
wait only ends when uvicorn cancels them, which it logs as errors. The CLI's
server calls ``begin_shutdown()`` as soon as the signal arrives; streams that
read their queues through ``get_until_shutdown`` then return within
``_POLL_SECONDS`` and the connections close cleanly.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

_stopping = threading.Event()
_POLL_SECONDS = 0.5

# Returned by get_until_shutdown once the server is stopping.
SHUTDOWN: Any = object()


def begin_shutdown() -> None:
    _stopping.set()


def is_shutting_down() -> bool:
    return _stopping.is_set()


def reset_shutdown() -> None:
    """For tests and in-process restarts."""
    _stopping.clear()


async def get_until_shutdown(queue: asyncio.Queue, timeout: float) -> Any:
    """The next item from ``queue``; None after ``timeout`` seconds; SHUTDOWN when the server stops."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        if _stopping.is_set():
            return SHUTDOWN
        remaining = deadline - loop.time()
        if remaining <= 0:
            return None
        try:
            return await asyncio.wait_for(queue.get(), timeout=min(_POLL_SECONDS, remaining))
        except TimeoutError:
            continue
