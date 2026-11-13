# Cron cheatsheet for CrabAgent schedules

The format is exactly five space-separated fields:
`minute hour day month weekday`. The scheduler is APScheduler, running in the
**server's local timezone**. Its rules differ from classic crontab in two important
ways.

| Field | Values | Notes |
|---|---|---|
| minute | 0–59 | `*/15` = every 15 minutes |
| hour | 0–23 | `9-17` = 09:00 through 17:00 |
| day | 1–31, `last` | `last` = last day of the month |
| month | 1–12 or `jan`–`dec` | |
| weekday | `mon`–`sun`, or 0–6 with **0 = Monday** | **Use names.** `1-5` is Tue–Sat here |

Syntax that works in any field: `*`, `a-b` (range), `a,b,c` (list), `*/n` or
`a-b/n` (step). No field may contain a space.

**Day and weekday are ANDed.** When both fields are restricted, the task runs only
on dates that match both. Classic cron runs when either one matches.

## Common schedules

| Request | Cron |
|---|---|
| Every day at 08:00 | `0 8 * * *` |
| Weekdays at 09:00 | `0 9 * * mon-fri` |
| Every Monday at 07:30 | `30 7 * * mon` |
| Every Friday at 16:00 | `0 16 * * fri` |
| Weekends at 10:00 | `0 10 * * sat,sun` |
| Every hour on the hour | `0 * * * *` |
| Every 2 hours during work hours on weekdays | `0 9-17/2 * * mon-fri` |
| Every 30 minutes | `*/30 * * * *` |
| 1st of every month at 06:00 | `0 6 1 * *` |
| Last day of the month at 17:00 | `0 17 last * *` |
| First Monday of the month at 10:00 | `0 10 1-7 * mon` (works because of AND) |
| Quarterly, 1st of Jan/Apr/Jul/Oct at 09:00 | `0 9 1 jan,apr,jul,oct *` |
| Once, Nov 14 at 15:00 (then self-delete) | `0 15 14 11 *` |

## Choosing a frequency
- Each firing is a full agent run, with model cost and up to 10 minutes. Polling
  every few minutes is rarely worth it. Hourly or slower suits most monitoring.
- Avoid putting many tasks on the same minute (`0 9 * * *`). Stagger them, e.g.
  `7 9 * * *`, so they don't all compete at once.
- If the user's timezone differs from the server's, convert the hour, then tell the
  user both times.
