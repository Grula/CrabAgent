// The wire format changed from an offset-less LOCAL string to an explicit UTC
// string ("...Z"). Both must resolve to the same instant, and a browser must
// never fall back to reading an offset-less value as its own local time.
// Assertions compare against Date.UTC / the Date object's own local getters so
// they hold in any host timezone.
import { describe, expect, it } from "vitest";
import {
  formatDate,
  formatLocalDateTime,
  formatTime,
  parseDateTime,
  toLocalDateStr,
} from "./time";

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

describe("parseDateTime", () => {
  it("reads an explicit UTC string as that instant", () => {
    const d = parseDateTime("2026-11-18T17:30:00.123456Z");
    expect(d?.getTime()).toBe(Date.UTC(2026, 10, 18, 17, 30, 0, 123));
  });

  it("reads an offset-less string as UTC, not browser-local", () => {
    const legacy = parseDateTime("2026-11-18T17:30:00.123456");
    expect(legacy?.getTime()).toBe(Date.UTC(2026, 10, 18, 17, 30, 0, 123));
    // Same instant as the new payload format.
    expect(legacy?.getTime()).toBe(parseDateTime("2026-11-18T17:30:00.123456Z")?.getTime());
  });

  it("keeps explicit non-UTC offsets exact", () => {
    expect(parseDateTime("2026-11-18T17:30:00+00:00")?.getTime()).toBe(Date.UTC(2026, 10, 18, 17, 30));
    expect(parseDateTime("2026-11-18T14:30:00-03:00")?.getTime()).toBe(Date.UTC(2026, 10, 18, 17, 30));
  });

  it("treats a date-only value as local midnight", () => {
    // A bare YYYY-MM-DD would otherwise mean UTC midnight and could show the
    // previous day in negative-offset zones.
    expect(parseDateTime("2026-11-18")?.getTime()).toBe(new Date(2026, 10, 18).getTime());
  });

  it("returns null for empty or unusable input", () => {
    expect(parseDateTime(null)).toBeNull();
    expect(parseDateTime(undefined)).toBeNull();
    expect(parseDateTime("")).toBeNull();
    expect(parseDateTime("not a date")).toBeNull();
  });
});

describe("local rendering", () => {
  it("formats the same wall clock the browser shows for a Z instant", () => {
    const iso = "2026-11-18T17:30:00Z";
    const asLocal = new Date(Date.UTC(2026, 10, 18, 17, 30));
    expect(formatTime(iso)).toBe(asLocal.toLocaleString());
    expect(formatDate(iso)).toBe(asLocal.toLocaleDateString());
  });

  it("formats an offset-less legacy string identically", () => {
    const iso = "2026-11-18T17:30:00";
    const asLocal = new Date(Date.UTC(2026, 10, 18, 17, 30));
    expect(formatTime(iso)).toBe(asLocal.toLocaleString());
    expect(formatDate(iso)).toBe(asLocal.toLocaleDateString());
  });

  it("derives the local date for grouping events into days", () => {
    const iso = "2026-11-19T01:30:00Z";
    const asLocal = new Date(Date.UTC(2026, 10, 19, 1, 30));
    expect(toLocalDateStr(iso)).toBe(
      `${asLocal.getFullYear()}-${pad(asLocal.getMonth() + 1)}-${pad(asLocal.getDate())}`,
    );
  });

  it("keeps a date-only value on its own day", () => {
    expect(toLocalDateStr("2026-11-18")).toBe("2026-11-18");
    // No invented "00:00": a date-only field stays date-only.
    expect(formatLocalDateTime("2026-11-18")).toBe("2026-11-18");
    expect(formatLocalDateTime("2026-11-18T17:30:00Z")).toBe(
      formatLocalDateTime("2026-11-18T17:30:00"),
    );
  });

  it("returns empty strings instead of Invalid Date", () => {
    expect(formatTime(null)).toBe("");
    expect(formatDate(undefined)).toBe("");
    expect(formatLocalDateTime("garbage")).toBe("");
    expect(toLocalDateStr("")).toBe("");
  });
});
