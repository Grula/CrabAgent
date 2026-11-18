"""Time handling for CrabAgent.

One rule: **DB storage and Python values are naive UTC**. Awareness exists only
at the input boundary (user supplied local wall clock), at display time, and on
the HTTP wire (ISO 8601 with ``Z``).
"""

from __future__ import annotations

import datetime


def utcnow() -> datetime.datetime:
    """Return the current naive UTC datetime, for DB storage."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def as_utc(dt: datetime.datetime | None) -> datetime.datetime | None:
    """Return ``dt`` as naive UTC; a naive input is assumed to already be UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(datetime.UTC).replace(tzinfo=None)


def from_local(dt: datetime.datetime | None) -> datetime.datetime | None:
    """Convert a naive/aware *local* datetime to naive UTC (input boundary)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.datetime.now().astimezone().tzinfo)
    return dt.astimezone(datetime.UTC).replace(tzinfo=None)


def to_local(dt: datetime.datetime | None) -> datetime.datetime | None:
    """Convert a naive/aware UTC datetime to a tz-aware local datetime (display only)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return dt.astimezone()


def iso_utc(dt: datetime.datetime | None) -> str | None:
    """Render ``dt`` as ISO 8601 naive UTC with a trailing ``Z``; ``None`` -> ``None``."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.UTC).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def to_epoch(dt: datetime.datetime | None) -> float | None:
    """Return epoch seconds for a naive-UTC datetime (host timezone is not assumed)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return dt.timestamp()
