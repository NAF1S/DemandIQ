# Phase 11 — Quarter-scale history and calendar events

## Outcome

Forecasts now train on a full year of history instead of the previous 104 days, and calendar events are a real model input rather than placeholder columns. Twenty-seven Bangladesh events for 2026 are loaded: Eid, Puja, national holidays and sale periods. The first run with both changes trained on 16,425 feature rows and saw Black Friday inside its 14-day forecast horizon.

## Why this changed

The previous window was 90 days of feature generation, which after accumulated rows meant 104 days of training data. That is one quarter, and it forced two compromises: yearly seasonality could never turn on (Prophet requires 365 rows), and entities near the 20-sales-day threshold could flip between LightGBM and Prophet on a single quiet fortnight.

Separately, the `ml_calendar_events` table existed but nothing read it. Every daily row carried `is_holiday = false` and `festival_name = null`, so the models could not learn that Eid or a flash sale lifts demand. Events are the one external signal that is known in advance, which makes them safe to use in a forward forecast — unlike traffic, which needs a forecast of its own.

## What changed

The default lookback is now 365 days across the Modal build, the training sequence and the CLI. The feature matrix spans 2025-11-14 → 2026-11-13 with 16,425 rows for 45 entities. This is enough for Prophet's yearly seasonality to activate, and it stabilises the sales-day counts that drive model selection.

Quarter features were added to the daily matrix and the model feature set: `quarter`, `day_of_quarter`, `days_to_quarter_end`, and `quarter_progress`. These let the model distinguish an end-of-quarter push from a quiet mid-quarter stretch, and they are computed from the date alone so they remain valid across the forecast horizon.

Calendar events are extracted from `ml_calendar_events` and expanded into one row per day. Each day gains `is_holiday`, `is_payday`, `is_festival_period`, `is_sale_event`, `event_impact`, `days_to_event` and `days_since_event`, plus the event name. Event distance is capped at 90 days so a quiet stretch does not dominate a tree split. The flags are persisted to `ml_daily_features`, so the event history is visible to every model and survives a rebuild.

Both model families consume events, each in the way its design supports. LightGBM receives the event flags and countdowns as features, and known future event dates are injected before prediction so a Black Friday inside the horizon actually changes the forecast. Prophet receives the same events as holiday regressors with a one-day window either side.

A new CLI path makes events operable. `python -m cli import-events --file data/calendar_bd_2026.csv` validates and loads a CSV, and `python -m cli events` reports what is loaded. The CSV needs `event_date,event_type,event_name` and accepts `impact_factor`. Unknown types and malformed dates fail with the line number rather than being silently skipped.

## The live run

The 2026 Bangladesh calendar was imported for org 1: 9 national holidays, 6 Eid days, 3 Puja days and 9 sale periods. The feature build wrote 16,425 rows with 720 event-flagged rows and 13 distinct named events. The training run (`run-20261113T062624Z`) reported one event inside the 14-day horizon — Black Friday on 2026-11-27 at impact 2.0 — and produced 630 forecasts from 2026-11-14 to 2026-11-27 with no past-dated rows.

With a year of history, three more products cleared the LightGBM threshold, so the live mix is now 8 Prophet and 2 velocity items, and 8 LightGBM, 10 Prophet, 12 funnel and 5 velocity products. The three earlier LightGBM products plus five newly qualifying ones each carry their own series.

## Evidence

The DemandIQ suite passes with 68 tests and one optional skip, including nine new calendar tests covering neutral calendars, festival and sale flags, payday, event countdown, forward-horizon coverage, type summaries, and graceful operation with no events. The portal suite remains at 157 passing tests.

A defect surfaced during the live run: when the year window changed model selection for several products, their previous Prophet rows stayed in `sp_demand_forecasts` under the old model version, inflating the table to 728 rows. The prune step now removes superseded model versions as well as past-dated rows, restoring exactly 630. The freshness endpoint reports `status: ok` with a zero-day feature lag and zero past-dated forecasts.

## Remaining risks

The calendar is seeded by me, not by the operator, so the Eid and Puja dates should be checked against the official 2026 calendar before anyone trusts the event impact. The `payday` type is supported end to end but has no rows yet, because salary dates vary by employer. Event impact is currently a static multiplier on the event day; a multi-day campaign ramp or a post-event dip is not modelled. And with only one year of history, yearly seasonality has exactly one cycle to learn from, so its estimates will be weak until a second year accumulates.

## How to verify

Run `python -m cli events` to see the loaded calendar, or query `ml_calendar_events` for org 1. Check `ml_daily_features` for `is_holiday` rows and confirm `holiday_name` is populated. Call `GET /api/forecasts/pipeline` to confirm the window and horizon, and inspect `sp_demand_forecasts` to confirm each entity has a single model version.
