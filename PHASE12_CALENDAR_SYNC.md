# Phase 12 — Automatic calendar sync

## Outcome

Calendar events no longer need to be typed in by hand. `python -m cli sync-events` and a Modal job pull Bangladesh public holidays and Islamic festival dates for any year, merge them with the recurring retail sale periods, and store them. A Modal schedule refreshes the next two years every January. The 2027 and 2028 calendars are already loaded: 24 events each, alongside the 27 from 2026.

## Why an automated source, and which one

Bangladesh has two kinds of relevant date, and no single source covers both.

Government public holidays are fixed and published: Independence Day, Bengali New Year, Victory Day and so on. Nager.Date (`date.nager.at`) exposes these as a free JSON API with no key, and it is the authoritative list for the fixed dates.

Islamic observances — Eid al-Fitr, Eid al-Adha, Ashura, Shab-e-Qadr, the Prophet's birthday — follow a lunar calendar, so their Gregorian date shifts every year and is confirmed by moon sighting shortly before the fact. The `holidays` Python package maintains a Bangladesh calendar that projects these forward and marks them explicitly as estimates.

Commercial sale periods — Black Friday, 12.12, the year-end run — appear in neither source, because they are retail decisions rather than public holidays. Those are built into the sync as a conservative default list.

The sync merges all three. Where both holiday sources name the same day differently, the merge keys on date and event type rather than name, so "Independence Day" and "Independence and National Day" collapse to one row instead of double-counting the day's impact. A genuine coincidence, such as a sale running on Eid, still produces two rows because the types differ.

## What changed

A new module (`app/etl/holiday_sync.py`) reads the `holidays` package, queries Nager.Date, and contributes the built-in sale periods. Network failure is not fatal: if Nager.Date is unreachable the sync falls back to the package alone, so a scheduled run never fails just because an external API is down.

Estimated dates carry an `(estimated)` suffix in the stored event name, so it is visible in the feature matrix and in the UI that a moon-sighting correction may move the date by a day or two. That suffix is a deliberate signal, not noise — an operator reviewing the calendar can see exactly which rows to re-check.

The importer is now idempotent. A unique index on the scope, date, type and name (`migrations/003_calendar_event_uniqueness.sql`) means re-running a sync for an overlapping year adds nothing. The second sync in this session wrote 48 rows and the table count did not change, which is the property that makes a scheduled yearly job safe.

Two Modal functions were added. `sync_calendar` runs on demand for any year list, and `annual_calendar_sync` runs on 1 January at 03:00 UTC to refresh the current and next year. The CLI mirrors both with `sync-events`, which supports `--years`, `--org`, `--no-sales`, `--replace`, `--export` and `--dry-run`.

`data/calendar_bd_2027.csv` was generated from these live sources as a reviewable artefact. It is the same data the sync writes, in the CSV format the manual importer accepts, so it can be edited and re-imported if a date needs correcting.

## The live result

The table now holds 75 events across three years: 27 for 2026 (seeded earlier), and 24 each for 2027 and 2028. By type there are 18 Eid days, 10 festival days, 23 holidays, 3 Puja days and 21 sale periods.

The 2027 file shows the shape of a typical year: Eid al-Fitr around 10–12 March, Eid al-Adha around 17–19 May, Independence Day on 26 March, Bengali New Year on 14 April, and the November–December sale run from 11.11 through Black Friday, 12.12 and the year-end period. Every Islamic date is marked estimated.

## Evidence

The DemandIQ suite passes with 79 tests and one optional skip, including nine new tests covering package extraction, the estimated-date markers, Eid classification and impact, sale period coverage, merge precedence, differently-named same-day collapsing, sale-on-holiday coexistence, sort order and CSV round-tripping.

The uniqueness index was applied to the live database and verified in `pg_indexes`. A repeated sync for 2027–2028 reported 48 written rows while the table stayed at 75, proving the de-duplication works against the real schema rather than only in tests.

## Remaining risks

The Islamic dates are projections and should be treated as approximate until the official moon-sighting announcement; the `(estimated)` suffix marks them, but nothing re-checks them automatically after publication. The built-in sale list is a reasonable default, not a record of what this store actually runs — if the store schedules different campaigns, those should be added manually and the defaults adjusted. Nager.Date is a free third-party service with no availability guarantee; the fallback keeps the sync working but would leave the fixed-date list dependent on the package until the next successful fetch. And the annual job relies on the `holidays` package being updated in the Modal image, so a year with a newly announced national holiday needs an image rebuild before the sync can pick it up.

## How to verify

Run `python -m cli sync-events --years 2029 --dry-run` to fetch a future year without writing, or `--export` to produce a CSV for review. Run `python -m cli events --org 1` to see the current inventory by type. In Modal, `modal run modal_app.py::sync_calendar --years 2029` performs a real sync, and the scheduled `annual_calendar_sync` appears in the deployed app.
