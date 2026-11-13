"""Event streams end on their own when the server begins shutting down."""

from __future__ import annotations

import asyncio
import time

import pytest

from crabagent.serve import shutdown


@pytest.fixture(autouse=True)
def _reset():
    shutdown.reset_shutdown()
    yield
    shutdown.reset_shutdown()


async def test_returns_items_and_none_on_timeout():
    queue: asyncio.Queue = asyncio.Queue()
    await queue.put("event")
    assert await shutdown.get_until_shutdown(queue, 1.0) == "event"
    started = time.monotonic()
    assert await shutdown.get_until_shutdown(queue, 0.2) is None
    assert time.monotonic() - started < 1.0


async def test_long_wait_ends_soon_after_shutdown_begins():
    queue: asyncio.Queue = asyncio.Queue()
    waiter = asyncio.create_task(shutdown.get_until_shutdown(queue, 30.0))
    await asyncio.sleep(0.1)
    started = time.monotonic()
    shutdown.begin_shutdown()
    assert await asyncio.wait_for(waiter, 2.0) is shutdown.SHUTDOWN
    assert time.monotonic() - started < 1.0


async def test_already_stopping_returns_at_once():
    shutdown.begin_shutdown()
    queue: asyncio.Queue = asyncio.Queue()
    await queue.put("late event")
    assert await shutdown.get_until_shutdown(queue, 30.0) is shutdown.SHUTDOWN
