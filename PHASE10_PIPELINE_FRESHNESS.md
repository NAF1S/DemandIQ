# Phase 10 — Keeping the forecast pipeline fed

## Outcome

The forecasting pipeline now feeds itself. A daily 01:00 UTC Modal job rebuilds the daily feature matrix from the live database, the 02:00 UTC training pass consumes it, and both training and the API refuse to present a stale pipeline as healthy. Before this change, features had frozen on 2026-10-30 while traffic and orders kept arriving, so every forecast written on 2026-11-13 covered dates that had already passed.

## Why this was needed

Phases 7 through 9 built training, evaluation and the manual “Compute now” path, but nothing refreshed the input. The Phase 5 feature build only existed as a CLI command that ran through a PHP bridge on the EC2 host, and Modal has no PHP. So each training pass reused the same 4,050 rows and produced a horizon anchored to the last feature date rather than to today.

The failure was silent. The forecasts were valid rows in the database, the risk table was populated, and the site looked healthy. The only symptom was that `forecast_date` values sat in the past: 725 of them by the time this phase started. A freshness check would have caught it on day three.

## What changed

Feature building can now run inside Modal. A new direct-Postgres extractor pulls the same datasets the PHP bridge produces — entities, consumption, traffic, site totals, referrers, product and item orders, returns, stock and reservations — and the frame-to-rows logic was extracted into a shared function so both paths produce identical output. The PHP bridge remains the path for local and EC2 CLI use; Modal uses the direct path because its database connection works.

Training now refuses to run on a stale matrix. If the newest feature date is more than two days old, the run fails with an explicit message telling the operator to build features first, and a compute-run row is marked failed with that reason instead of reporting progress. Two days is the allowance for today's orders still arriving; anything beyond that means the horizon would be entirely historical.

Forecast rows are pruned to the new horizon. Because forecasts are keyed by entity, date and model version, a run that starts later than the previous one would otherwise leave the old past-dated rows in place. Each entity's rows before the new first forecast date are now deleted, except the legacy `baseline-v1` prototype rows, which are cleaned up separately.

Freshness is now observable three ways: a Modal `health` function that raises when the pipeline is stale, a `python -m cli health` subcommand, and a portal endpoint at `GET /api/forecasts/pipeline` that the SPA can call. All three report feature lag in days, forecast and risk age in hours, and the count of past-dated forecast rows.

## The live run

The first direct feature build wrote 4,050 rows for 45 entities covering 2026-08-16 → 2026-11-13. The combined build-and-train function then trained from the refreshed 4,680-row matrix, which spans 2026-08-02 → 2026-11-13.

The result is a genuinely forward forecast. Each of the 45 entities now has 14 forecast days from 2026-11-14 through 2026-11-27, with zero past-dated rows among the 630 pipeline forecasts. The live endpoint reports `status: ok` with a feature lag of zero days, forecast age of zero hours, and 45 fresh risk rows. Model selection is unchanged from the previous phase: 8 Prophet and 2 velocity items, and 3 LightGBM, 11 Prophet, 15 funnel and 5 velocity products.

## Evidence

The DemandIQ suite passes with 59 tests and one optional skip, including the new horizon-guard, feature-span and feature-row tests. The portal suite passes with 157 tests and 776 assertions, including five freshness cases covering the healthy, stale-feature, past-dated-forecast, legacy-baseline and tenant-scoping paths. The SPA type check is clean.

Two defects were found and fixed during the live runs. The referrer query's `ILIKE '%search%'` collided with psycopg's `%` placeholder syntax and failed the first build; it is now escaped. And the first refresh produced 28 forecast days per entity because the new horizon was appended beside the old one; the pruning step fixed that, which is why the second run ends with exactly 14 days per entity.

## Remaining risks

The 140 legacy `baseline-v1` rows are still present and deliberately excluded from the freshness count; a demand chart must filter them or they should be retired. The daily 01:00 and 02:00 UTC jobs are deployed but have not yet been observed on a real clock, so the first unattended cycle is the thing to watch. The freshness endpoint is not yet rendered in the SPA; it is available to call and is the natural place to add a staleness banner to the Forecasts page.

## How to verify

Call `GET /api/forecasts/pipeline` as a manager and confirm `status: ok`, `feature_lag_days: 0`, and `past_forecast_rows: 0`. Run `modal run modal_app.py::health` for the same check from the training side, and `modal run modal_app.py::features_and_training` to exercise the full refresh-plus-train sequence.
