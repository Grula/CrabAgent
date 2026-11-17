import asyncio
import os
import tempfile
from pathlib import Path

import pytest

# Tests must never touch the user's real database (~/.crabagent/crabagent.db).
# Point CrabAgent at a throwaway one before anything imports crabagent: its
# engine is created at import time from CRAB_DB_URL.
_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="crabagent-tests-"))
os.environ.setdefault("CRAB_DB_URL", f"sqlite+aiosqlite:///{_TEST_DB_DIR / 'crabagent.db'}")


def _create_test_tables() -> None:
    from crabagent.core import database

    async def create():
        async with database.engine.begin() as conn:
            await conn.run_sync(database.Base.metadata.create_all)
        await database.engine.dispose()  # don't carry pooled connections across event loops

    asyncio.run(create())


_create_test_tables()


@pytest.fixture
def anyio_backend():
    return "asyncio"
