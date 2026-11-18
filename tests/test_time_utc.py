"""Contract tests for the naive-UTC storage migration.

CrabAgent stores every SQLite ``DateTime`` value as a *naive* datetime. Today
those values are naive **local** wall clock (``core/database.py:21``
``utcnow()`` returns ``datetime.datetime.now()``); the migration switches the
storage frame to naive **UTC**.

The contract these tests pin down::

    crabagent.core.timeutil
        utcnow()       -> naive UTC (tzinfo is None), for DB storage
        as_utc(dt)     -> naive or aware -> naive UTC; naive input ASSUMED UTC; None passes through
        from_local(dt) -> naive or aware LOCAL -> naive UTC (user-input boundary)
        to_local(dt)   -> naive UTC -> tz-aware local datetime (display only)
        iso_utc(dt)    -> 'YYYY-MM-DDTHH:MM:SS.ffffffZ'; None -> None
        to_epoch(dt)   -> naive UTC -> epoch seconds

    crabagent.core.database.utcnow stays importable and returns the same value
    as timeutil.utcnow().

Rules: storage and Python values are naive UTC; awareness exists only at input,
at display and on the HTTP wire.

Every test below fails against the current tree for a value reason (a timestamp
off by the host offset) or because a symbol the contract requires is missing;
none of them fail because the test itself is broken. They must all pass once
the migration lands.

Run with::

    PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider tests/test_time_utc.py
"""

from __future__ import annotations

import datetime
import importlib
import re
import sys
import types

import pytest
import sqlalchemy as sa
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from crabagent.core import database as db_module
from crabagent.core.database import Base


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _offset() -> datetime.timedelta:
    """Host offset, ``local - UTC`` (negative west of Greenwich).

    Rounded to whole seconds: the two ``now()`` calls differ by microseconds and
    the offset is used for exact datetime comparisons.
    """
    raw = datetime.datetime.now() - datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    return datetime.timedelta(seconds=round(raw.total_seconds()))


def _utcnow_naive() -> datetime.datetime:
    """The naive-UTC value every DB timestamp is expected to hold."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _timeutil():
    """Import ``crabagent.core.timeutil``; a missing module is an AssertionError.

    Raising AssertionError (not ImportError) keeps the failure readable and
    names the symbols the migration has to provide.
    """
    try:
        return importlib.import_module("crabagent.core.timeutil")
    except ImportError as exc:  # pragma: no cover - the state of the tree today
        raise AssertionError(
            "expected module crabagent.core.timeutil providing "
            "utcnow/as_utc/from_local/to_local/iso_utc/to_epoch; it does not exist yet "
            f"({exc})"
        ) from None


def _as_dt(raw) -> datetime.datetime:
    if isinstance(raw, datetime.datetime):
        return raw
    return datetime.datetime.fromisoformat(str(raw))


@pytest.fixture
async def session_factory(tmp_path, monkeypatch):
    """Temp SQLite DB with the real schema; the module-level factory is patched.

    Nothing here touches the user's live ``~/.crabagent/crabagent.db``.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'utc-test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "async_session_factory", factory)
    monkeypatch.setattr(db_module, "engine", engine, raising=False)
    yield factory
    await engine.dispose()


# ---------------------------------------------------------------------------
# 1. The storage clock itself
# ---------------------------------------------------------------------------

async def test_database_utcnow_is_naive_utc():
    """``database.utcnow()`` must be naive UTC, not naive local wall clock."""
    stamp = db_module.utcnow()

    assert stamp.tzinfo is None, f"storage clock must be naive, got tzinfo={stamp.tzinfo!r}"

    drift_utc = abs((stamp - _utcnow_naive()).total_seconds())
    assert drift_utc < 2, (
        f"database.utcnow()={stamp.isoformat()} is {drift_utc / 60:.0f} min away from UTC "
        f"({_utcnow_naive().isoformat()}) - it is still the local wall clock"
    )

    offset = _offset()
    if offset == datetime.timedelta(0):
        print("host offset is zero; skipping the local-vs-UTC discrimination assertion")
    else:
        drift_local = abs((stamp - datetime.datetime.now()).total_seconds())
        assert drift_local > 2, (
            f"database.utcnow()={stamp.isoformat()} is within {drift_local:.1f}s of naive local "
            f"now ({datetime.datetime.now().isoformat()}) on a host with offset {offset}"
        )


# ---------------------------------------------------------------------------
# 2. ORM column defaults
# ---------------------------------------------------------------------------

async def test_model_default_created_at_is_utc(session_factory):
    """A row inserted through a model default stores UTC, not local wall clock."""
    from crabagent.core.database import ToolInvocation

    utc_now = _utcnow_naive()
    async with session_factory() as db:
        db.add(ToolInvocation(user_id=1, tool_name="tz_probe"))
        await db.commit()
        row = (await db.execute(select(ToolInvocation))).scalars().one()

    assert row.created_at.tzinfo is None
    drift = abs((row.created_at - utc_now).total_seconds())
    assert drift <= 5, (
        f"ToolInvocation.created_at={row.created_at.isoformat()} is {drift / 60:.0f} min from UTC "
        f"({utc_now.isoformat()}); the ORM default still writes the local wall clock"
    )


# ---------------------------------------------------------------------------
# 3. app_settings: raw-SQL writers must land in the same frame as the ORM
# ---------------------------------------------------------------------------

async def test_app_settings_raw_writes_are_utc(session_factory):
    """fts ``_upsert_setting`` and the scheduler lock store UTC like the ORM.

    Every ``app_settings`` writer must use one clock and one format, otherwise
    the table keeps the local/UTC mix that hid this bug.
    """
    from crabagent.core.database import AppSetting
    from crabagent.core.fts import _upsert_setting
    from crabagent.serve.scheduler import SchedulerService

    async with session_factory() as db:
        db.add(AppSetting(key="orm_probe", value="1"))
        await db.commit()
        await _upsert_setting(db, "fts_probe", "2")
        await db.commit()

    sched = SchedulerService.__new__(SchedulerService)  # no real APScheduler
    sched._own_pid = 424242
    assert await sched._acquire_process_lock("tz_probe", ttl_seconds=60) is True

    utc_now = _utcnow_naive()
    keys = ("orm_probe", "fts_probe", "_lock:tz_probe")
    async with session_factory() as db:
        rows = dict(
            (
                await db.execute(
                    text(
                        "SELECT key, updated_at FROM app_settings "
                        "WHERE key IN ('orm_probe', 'fts_probe', '_lock:tz_probe')"
                    )
                )
            ).fetchall()
        )

    observed: dict[str, datetime.datetime] = {}
    for key in keys:
        assert key in rows, f"app_settings[{key}] was never written"
        raw = str(rows[key])
        parsed = _as_dt(rows[key])
        observed[key] = parsed
        drift = abs((parsed - utc_now).total_seconds())
        assert drift <= 2, (
            f"app_settings[{key}].updated_at={raw!r} is {drift / 60:.0f} min from UTC "
            f"({utc_now.isoformat()}); that writer is still on the local clock"
        )

    # All three writers fired within milliseconds of each other, so any spread
    # bigger than a couple of seconds means two different frames in one table.
    spread = (max(observed.values()) - min(observed.values())).total_seconds()
    assert spread < 2, (
        f"app_settings holds mixed frames: ORM/fts/scheduler timestamps written in the same "
        f"instant are {spread / 60:.0f} min apart ({ {k: v.isoformat() for k, v in observed.items()} })"
    )


# ---------------------------------------------------------------------------
# 4. Wire helper
# ---------------------------------------------------------------------------

async def test_iso_utc_emits_zulu_and_roundtrips():
    """``iso_utc`` renders a naive-UTC value with an explicit ``Z``."""
    tu = _timeutil()

    value = datetime.datetime(2026, 11, 20, 17, 30, 0, 123456)
    out = tu.iso_utc(value)

    assert out == "2026-11-20T17:30:00.123456Z", f"iso_utc({value.isoformat()}) returned {out!r}"
    parsed = datetime.datetime.fromisoformat(out)
    assert parsed.tzinfo is not None and parsed.utcoffset() == datetime.timedelta(0)
    assert parsed.replace(tzinfo=None) == value, "iso_utc() must not shift the value"
    assert tu.iso_utc(None) is None


# ---------------------------------------------------------------------------
# 5. Task deadlines: browser input is local, storage is UTC
# ---------------------------------------------------------------------------

async def _create_task_via_api(factory, deadline: str) -> None:
    """Drive the real POST /tasks handler (the browser sends a datetime-local string)."""
    from crabagent.serve.api.task import CreateTaskRequest, create_task

    async with factory() as db:
        await create_task(
            CreateTaskRequest(title="deadline probe", deadline=deadline),
            user=types.SimpleNamespace(id=1),
            db=db,
        )


async def _stored_deadline(factory) -> datetime.datetime:
    from crabagent.core.database import Task

    async with factory() as db:
        task = (await db.execute(select(Task).where(Task.title == "deadline probe"))).scalars().one()
        return task.deadline


async def test_task_timed_deadline_roundtrip_is_utc(session_factory):
    """``2026-11-20T14:30`` local is stored as UTC and renders back as 14:30 local."""
    offset = _offset()
    local = datetime.datetime(2026, 11, 20, 14, 30)

    await _create_task_via_api(session_factory, "2026-11-20T14:30")
    stored = await _stored_deadline(session_factory)

    assert stored == local - offset, (
        f"stored deadline {stored.isoformat()} != {(local - offset).isoformat()} "
        f"({local.isoformat()} local minus offset); the POST handler stores local wall clock"
    )
    assert stored.tzinfo is None

    tu = _timeutil()
    rendered = tu.to_local(stored).strftime("%Y-%m-%d %H:%M")
    assert rendered == "2026-11-20 14:30", f"round-trip rendered {rendered!r} instead of the typed value"


async def test_task_date_only_deadline_keeps_local_date(session_factory):
    """The date-only form ``YYYY-MM-DD`` comes back as that same local date at 00:00."""
    offset = _offset()
    local_midnight = datetime.datetime(2026, 11, 20, 0, 0)

    await _create_task_via_api(session_factory, "2026-11-20")
    stored = await _stored_deadline(session_factory)

    assert stored == local_midnight - offset, (
        f"stored date-only deadline {stored.isoformat()} != {(local_midnight - offset).isoformat()}; "
        "a date-only deadline must be stored as the UTC instant of local midnight"
    )

    tu = _timeutil()
    back = tu.to_local(stored)
    assert (back.year, back.month, back.day) == (2026, 11, 20), f"local date drifted to {back.isoformat()}"
    assert (back.hour, back.minute) == (0, 0), f"date-only deadline came back at {back.isoformat()}"


# ---------------------------------------------------------------------------
# 6. Calendar: midnight == all-day must be judged in the local frame
# ---------------------------------------------------------------------------

async def test_all_day_task_deadline_still_reads_as_all_day(session_factory):
    """A task due at local midnight is still an all-day entry once deadlines are UTC.

    ``core/calendar/store.py`` decides all-day by ``deadline.hour == 0``. With UTC
    storage the stored instant is 03:00 on a UTC-3 host, so the check has to look
    at the local rendering instead.
    """
    from crabagent.core.calendar.store import list_events
    from crabagent.core.task.store import add_task

    offset = _offset()
    local_midnight = datetime.datetime(2026, 11, 20, 0, 0)
    utc_deadline = local_midnight - offset  # what the fix stores for local date 2026-11-20
    day_start = utc_deadline
    day_end = day_start + datetime.timedelta(days=1)

    async with session_factory() as db:
        await add_task(db, user_id=1, title="all-day probe", deadline=utc_deadline)
        events = await list_events(db, 1, day_start, day_end)

    probe = [e for e in events if e.get("type") == "task"]
    assert len(probe) == 1, f"all-day probe missing from the day range, got {len(probe)} task entries"
    entry = probe[0]
    assert entry["all_day"] is True, (
        f"task deadline stored as {utc_deadline.isoformat()} (local midnight) was reported with "
        f"all_day={entry['all_day']!r}; the midnight=all-day check still reads the raw UTC hour"
    )

    assert entry["start_time"].endswith("Z"), (
        f"task entry start_time={entry['start_time']!r} must be ISO UTC like the persisted events"
    )
    # start_time is ISO UTC with a trailing 'Z' (aware after fromisoformat), so
    # normalise it to naive UTC before applying the host offset.
    start_local = _as_dt(entry["start_time"]).replace(tzinfo=None) + offset
    assert start_local == local_midnight, (
        f"all-day entry starts at {start_local.isoformat()} local, expected {local_midnight.isoformat()}"
    )


# ---------------------------------------------------------------------------
# 7. iCal sync cursor must stamp the real UTC date
# ---------------------------------------------------------------------------

_PROPFIND_XML = (
    "<A:multistatus>"
    "<A:response><A:prop><A:resourcetype><B:calendar/></A:resourcetype></A:prop>"
    "<A:href>/cal/123/</A:href></A:response>"
    "</A:multistatus>"
)
_REPORT_XML = "<D:multistatus><D:response><D:href>/cal/123/ev.ics</D:href></D:response></D:multistatus>"
_EMPTY_ICS = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//probe//EN\r\nEND:VCALENDAR\r\n"


class _FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeHTTPXClient:
    """Records the CalDAV REPORT body so the time-range cursor can be inspected."""

    reports: list[str] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def request(self, method, url, headers=None, content=None):
        if method == "PROPFIND":
            return _FakeResponse(_PROPFIND_XML)
        _FakeHTTPXClient.reports.append(content.decode())
        return _FakeResponse(_REPORT_XML, status_code=207)

    def get(self, url, headers=None, timeout=None):
        return _FakeResponse(_EMPTY_ICS, status_code=200)


class _FakeCalendarObject:
    def walk(self, name):
        return []


def _install_fake_http_and_icalendar(monkeypatch) -> None:
    httpx_mod = types.ModuleType("httpx")
    httpx_mod.Client = _FakeHTTPXClient
    ical = types.ModuleType("icalendar")

    class _FakeCalendar:
        @staticmethod
        def from_ical(_text):
            return _FakeCalendarObject()

    ical.Calendar = _FakeCalendar
    monkeypatch.setitem(sys.modules, "httpx", httpx_mod)
    monkeypatch.setitem(sys.modules, "icalendar", ical)


async def test_ical_sync_cursor_stamps_real_utc_date(session_factory, monkeypatch):
    """The ``%Y%m%dT000000Z`` cursor must name the UTC date, not the local one.

    The clock is frozen at 2026-11-18 22:30 local, which is 2026-11-19 01:30 UTC
    on a UTC-3 host: a local-date cursor says 20261118, the correct UTC date is
    20261119.
    """
    from crabagent.core.calendar import ical_sync
    from crabagent.core.calendar.ical_sync import _sync_caldav_source
    from crabagent.core.database import CalendarIcalSource

    offset = _offset()
    frozen_local = datetime.datetime(2026, 11, 18, 22, 30, 0)
    frozen_utc = frozen_local - offset

    class _FrozenDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return frozen_local
            return frozen_utc.replace(tzinfo=tz)

        @classmethod
        def utcnow(cls):
            return frozen_utc

    shim = types.SimpleNamespace(
        datetime=_FrozenDatetime,
        timedelta=datetime.timedelta,
        date=datetime.date,
        time=datetime.time,
    )
    monkeypatch.setattr(ical_sync, "datetime", shim)
    monkeypatch.setattr(db_module, "utcnow", lambda: frozen_utc)
    timeutil_mod = sys.modules.get("crabagent.core.timeutil")
    if timeutil_mod is not None:
        monkeypatch.setattr(timeutil_mod, "utcnow", lambda: frozen_utc, raising=False)
    if hasattr(ical_sync, "utcnow"):
        monkeypatch.setattr(ical_sync, "utcnow", lambda: frozen_utc)

    _FakeHTTPXClient.reports = []
    _install_fake_http_and_icalendar(monkeypatch)

    async with session_factory() as db:
        source = CalendarIcalSource(
            user_id=1,
            name="probe",
            url="caldav.example.com",
            source_type="caldav",
            caldav_username="u",
            caldav_password="p",
            lookback_days=0,
            lookahead_days=0,
        )
        db.add(source)
        await db.commit()
        await db.refresh(source)
        await _sync_caldav_source(db, source)

    assert _FakeHTTPXClient.reports, "the CalDAV REPORT was never issued, the probe did not run"
    match = re.search(r"start='([^']+)'", _FakeHTTPXClient.reports[0])
    assert match, f"no time-range start in the REPORT body: {_FakeHTTPXClient.reports[0]!r}"
    emitted = match.group(1)

    expected = frozen_utc.strftime("%Y%m%dT000000Z")
    assert emitted == expected, (
        f"CalDAV cursor start={emitted!r} but the real UTC date is {expected!r} "
        f"(the local clock reads {frozen_local:%Y%m%d %H:%M}); a local date is being stamped with Z"
    )


# ---------------------------------------------------------------------------
# 8. One-shot migration of legacy local rows
# ---------------------------------------------------------------------------

_MIGRATION_CANDIDATES = (
    ("crabagent.core.database", "migrate_timestamps_to_utc"),
    ("crabagent.core.database", "migrate_local_timestamps_to_utc"),
    ("crabagent.core.database", "migrate_datetimes_to_utc"),
    ("crabagent.core.timeutil", "migrate_timestamps_to_utc"),
    ("crabagent.core.migrations", "migrate_timestamps_to_utc"),
)


def _migration_entrypoint():
    """Resolve the awaited one-shot migration, or fail naming the expected symbol."""
    for module_name, attr in _MIGRATION_CANDIDATES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        fn = getattr(module, attr, None)
        if fn is not None:
            return fn
    raise AssertionError(
        "expected a one-shot naive-UTC migration entrypoint such as "
        "crabagent.core.database.migrate_timestamps_to_utc() - awaitable, no arguments, "
        "running against database.async_session_factory / database.engine and guarded by a "
        "marker row in app_settings. None of "
        f"{[f'{m}.{a}' for m, a in _MIGRATION_CANDIDATES]} exists"
    )


def _datetime_columns() -> list[tuple[str, str, str]]:
    out = []
    for table in Base.metadata.sorted_tables:
        pk = list(table.primary_key.columns)[0].name
        for col in table.columns:
            if isinstance(col.type, sa.DateTime):
                out.append((table.name, col.name, pk))
    return out


async def _app_setting_keys(engine) -> set[str]:
    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT key FROM app_settings"))).fetchall()
    return {str(r[0]) for r in rows}


async def _snapshot(engine) -> dict:
    """Read every DateTime column plus the agent_runs epoch column."""
    data: dict = {"datetimes": {}, "epochs": {}}
    async with engine.connect() as conn:
        for table, col, pk in _datetime_columns():
            rows = (await conn.execute(text(f"SELECT {pk}, {col} FROM {table}"))).fetchall()
            for row_id, value in rows:
                if value is not None:
                    data["datetimes"][(table, str(row_id), col)] = _as_dt(value)
        epoch_rows = (
            await conn.execute(text("SELECT id, started_at FROM agent_runs ORDER BY id"))
        ).fetchall()
        for row_id, value in epoch_rows:
            data["epochs"][("agent_runs", str(row_id), "started_at")] = value
    return data


async def test_migration_shifts_legacy_local_rows_to_utc(tmp_path, monkeypatch):
    """The migration rewrites legacy local values to UTC once, and is idempotent."""
    from crabagent.core.database import AgentRun, AppSetting, CalendarEvent, Task, ToolInvocation

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "async_session_factory", factory)
    monkeypatch.setattr(db_module, "engine", engine, raising=False)

    offset = _offset()
    legacy = datetime.datetime.now()  # the legacy writer's frame: naive LOCAL
    epoch = 1_794_507_703.25  # agent_runs.started_at is a Float epoch, already absolute

    async with factory() as db:
        db.add(AppSetting(key="legacy_setting", value="x", updated_at=legacy))
        db.add(ToolInvocation(user_id=1, tool_name="legacy_tool", created_at=legacy))
        db.add(
            Task(
                user_id=1,
                title="legacy task",
                deadline=legacy,
                created_at=legacy,
                updated_at=legacy,
            )
        )
        db.add(
            AgentRun(
                user_id=1,
                agent_name="legacy-agent",
                started_at=epoch,
                created_at=legacy,
            )
        )
        db.add(
            CalendarEvent(
                user_id=1,
                title="legacy event",
                start_time=legacy,
                end_time=legacy,
                created_at=legacy,
                updated_at=legacy,
            )
        )
        await db.commit()

    before = await _snapshot(engine)
    assert before["datetimes"], "no legacy DateTime values were seeded"
    assert before["epochs"], "no legacy epoch value was seeded"
    keys_before = await _app_setting_keys(engine)

    migrate = _migration_entrypoint()
    await migrate()
    after = await _snapshot(engine)

    for key, old in before["datetimes"].items():
        assert key in after["datetimes"], f"{key} disappeared during the migration"
        new = after["datetimes"][key]
        expected = old - offset
        assert abs((new - expected).total_seconds()) < 1, (
            f"{key[0]}.{key[2]} (id {key[1]}): {old.isoformat()} -> {new.isoformat()}, "
            f"expected {expected.isoformat()} (legacy local value shifted by {offset})"
        )

    assert after["epochs"] == before["epochs"], (
        "Float epoch columns must not move: agent_runs.started_at "
        f"{before['epochs']} -> {after['epochs']}"
    )

    await migrate()
    again = await _snapshot(engine)
    for key, moved in after["datetimes"].items():
        if key not in before["datetimes"]:
            continue  # marker rows are the migration's own bookkeeping
        assert again["datetimes"][key] == moved, (
            f"second migration run shifted {key[0]}.{key[2]} again: "
            f"{moved.isoformat()} -> {again['datetimes'][key].isoformat()}"
        )

    marker = sorted((await _app_setting_keys(engine)) - keys_before)
    assert any(
        "utc" in key.lower() and ("time" in key.lower() or "ts" in key.lower()) for key in marker
    ), (
        "expected an app_settings row keyed like 'timestamps_utc_migrated' to mark the one-shot "
        f"migration as done; new app_settings keys seen: {marker}"
    )


# ---------------------------------------------------------------------------
# 9. Tool-usage window: the since_days cutoff is a UTC cutoff
# ---------------------------------------------------------------------------

async def test_tool_usage_since_days_cutoff_is_utc(session_factory):
    """``aggregate_tool_usage`` compares UTC cutoffs against UTC rows.

    Rows are seeded relative to the *writer's* clock (``database.utcnow()``), so
    the fixture is correct in both frames. A row 29d23h old must stay inside a
    30-day window; today the UTC cutoff is measured against local rows and the
    boundary row drops out.
    """
    from crabagent.core.agent.tool_telemetry import aggregate_tool_usage
    from crabagent.core.database import ToolInvocation

    writer_now = db_module.utcnow()  # local wall clock today, UTC after the fix
    rows = {
        "probe_now": None,  # ORM default writes "now" in the writer's frame
        "probe_recent_29d23h": writer_now - datetime.timedelta(days=29, hours=23),
        "probe_old_40d": writer_now - datetime.timedelta(days=40),
    }
    async with session_factory() as db:
        for tool_name, created in rows.items():
            kwargs = {"user_id": 1, "tool_name": tool_name}
            if created is not None:
                kwargs["created_at"] = created
            db.add(ToolInvocation(**kwargs))
        await db.commit()
        stats = await aggregate_tool_usage(db, since_days=30)

    seen = {s["tool_name"] for s in stats}
    assert "probe_now" in seen, f"a row created now fell outside the 30-day window: {sorted(seen)}"
    assert "probe_recent_29d23h" in seen, (
        "a row 29d23h old fell outside the 30-day window "
        f"(cutoff frame is not UTC): {sorted(seen)}"
    )
    assert "probe_old_40d" not in seen, f"a 40-day-old row leaked into the 30-day window: {sorted(seen)}"
    assert stats, "aggregate_tool_usage returned nothing"


# ---------------------------------------------------------------------------
# 10. HTTP wire format
# ---------------------------------------------------------------------------

async def test_wire_datetimes_carry_utc_offset_and_dates_stay_dates(session_factory):
    """Emitted datetimes carry ``Z``/``+00:00``; date-only fields stay bare dates."""
    from crabagent.core.calendar.store import add_event, get_today_overview
    from crabagent.core.task.store import add_task

    offset = _offset()
    timed_utc = datetime.datetime(2026, 11, 20, 17, 30)  # 14:30 local on a UTC-3 host
    day_utc = datetime.datetime(2026, 11, 20, 3, 0)  # 00:00 local on a UTC-3 host
    assert day_utc == datetime.datetime(2026, 11, 20, 0, 0) - offset

    async with session_factory() as db:
        timed_task = await add_task(db, user_id=1, title="timed probe", deadline=timed_utc)
        day_task = await add_task(db, user_id=1, title="day probe", deadline=day_utc)
        event = await add_event(
            db,
            user_id=1,
            title="event probe",
            start_time=timed_utc,
            end_time=timed_utc + datetime.timedelta(hours=1),
        )
        overview = await get_today_overview(db, 1)

    assert timed_task["deadline"].endswith(("Z", "+00:00")), (
        f"task payload deadline={timed_task['deadline']!r} carries no UTC offset"
    )
    assert event["start_time"].endswith(("Z", "+00:00")), (
        f"calendar payload start_time={event['start_time']!r} carries no UTC offset"
    )

    tu = _timeutil()
    rendered = tu.to_local(timed_utc).strftime("%Y-%m-%d %H:%M")
    assert rendered == "2026-11-20 14:30", f"local rendering drifted: {rendered!r}"

    assert day_task["deadline"] == "2026-11-20", (
        f"date-only task deadline must stay 'YYYY-MM-DD', got {day_task['deadline']!r}"
    )
    assert "T" not in day_task["deadline"] and not day_task["deadline"].endswith(("Z", "+00:00"))

    assert "T" not in overview["date"] and "+" not in overview["date"], (
        f"calendar 'date' must stay a bare local date, got {overview['date']!r}"
    )
