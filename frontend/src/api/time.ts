/**
 * Time helpers — the single parse path for datetimes that come over the wire.
 *
 * The backend stores and serializes every datetime as an explicit UTC instant
 * ("2026-11-18T17:30:00.123456Z"). Payloads produced before that change had no
 * timezone designator but were also UTC, so an absent designator is read as
 * UTC — never as browser-local, which would shift the instant by the host
 * offset. Date-only values ("2026-11-18") describe a calendar day rather than
 * an instant and are parsed as *local* midnight, so the day never shifts
 * (`new Date("2026-11-18")` would mean UTC midnight and can land on the
 * previous day in negative-offset zones).
 *
 * Display is always local: parse once, then use the Date's local getters.
 */

const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/;
// Trailing "Z", "+02:00" or "+0200". A date-only "2026-11-18" never matches
// (the sign would have to be followed by four digits).
const TZ_DESIGNATOR_RE = /(?:Z|[+-]\d{2}:?\d{2})$/i;

function hasTimezone(isoStr: string): boolean {
  return TZ_DESIGNATOR_RE.test(isoStr);
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function localDatePart(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/**
 * Parse a wire datetime (or date-only value) into a Date, or `null` when the
 * input is empty or unusable. This is the one place that knows how the wire
 * formats time — every other site should parse through it.
 */
export function parseDateTime(isoStr: string | null | undefined): Date | null {
  if (!isoStr) return null;
  const raw = isoStr.trim();
  if (!raw) return null;
  if (DATE_ONLY_RE.test(raw)) return new Date(`${raw}T00:00:00`); // local midnight
  const normalized = raw.replace(/^(\d{4}-\d{2}-\d{2}) /, "$1T");
  const d = new Date(hasTimezone(normalized) ? normalized : `${normalized}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Local calendar day ("YYYY-MM-DD") of a wire value; "" when unusable. */
export function toLocalDateStr(isoStr: string | null | undefined): string {
  const d = parseDateTime(isoStr);
  return d ? localDatePart(d) : "";
}

/**
 * Local date and time ("YYYY-MM-DD HH:MM") of a wire value; "" when unusable.
 * Date-only values stay date-only — they carry no time of day.
 */
export function formatLocalDateTime(isoStr: string | null | undefined): string {
  const d = parseDateTime(isoStr);
  if (!d) return "";
  if (DATE_ONLY_RE.test((isoStr ?? "").trim())) return localDatePart(d);
  return `${localDatePart(d)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function formatTime(isoStr: string | null | undefined): string {
  const d = parseDateTime(isoStr);
  return d ? d.toLocaleString() : "";
}

export function formatDate(isoStr: string | null | undefined): string {
  const d = parseDateTime(isoStr);
  return d ? d.toLocaleDateString() : "";
}

export function formatTimeShort(isoStr: string | null | undefined): string {
  const d = parseDateTime(isoStr);
  if (!d) return "";
  const now = new Date();
  const diffDays = (now.getTime() - d.getTime()) / 86400000;
  if (diffDays < 1 && d.getDate() === now.getDate()) {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } else if (diffDays < 2) {
    return "Yesterday" + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString([], { month: "2-digit", day: "2-digit" }) + " " +
         d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
