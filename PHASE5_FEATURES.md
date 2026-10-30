# Phase 5 — Daily Features

Phase 5 adds the zero-filled daily feature matrix that Phase 6/7 will train on.
The code is complete and covered by tests; the live population is currently
blocked by the Supabase Supavisor pooler, which drops connections mid-query
(`server closed the connection unexpectedly`) on `sp_item_movements` and
`sp_traffic_daily`.

## What was built

- `app/etl/features.py` — pure feature builder. It produces a continuous daily
  frame with zero-filled demand/traffic, forward-filled stock, net consumption,
  calendar flags (Friday/Saturday weekend), lags (1/7/14/28), rolling windows
  (7/14/30 mean/std/max), trends, average order value, product/site funnel
  ratios, channel shares and traffic momentum.
- `app/etl/quality.py` — sparsity, gap, coefficient-of-variation and
  model recommendation (`velocity`, `lightgbm`, `prophet`).
- `app/etl/pipeline.py` — per-entity orchestrator. Products use order-line units
  and return quantities; items use movement confirmations/returns. Orders,
  stock snapshots, reservations, product traffic, site traffic and referrers
  are joined per entity, then written to `ml_daily_features`.
- `app/etl/psql.py` — extraction/loading bridge. The portal PHP scripts
  (`portal/scripts/phase5_extract.php` and `phase5_write.php`) perform the SQL
  boundary; pandas performs the feature math.
- `app/etl/extractors.py` — retained async extractors, including the batched
  all-entity stock, reservation, order, return and traffic queries.
- `cli.py` — `python -m cli build-features --org 1 --store 1 --lookback-days 90`
  (`--dry-run`, `--json`, `--dsn`).

## Signals

The expected live shape is 45 active entities (10 items, 35 products, store 1)
over 90 days = 4,050 daily rows. A dry-run reached that shape with 30 products
having traffic and 22 of 45 entities having enough history for a model:

- `prophet`: 9
- `lightgbm`: 13
- `velocity`: 23

These counts come from the dry-run and have not been persisted.

## Tests

`/tmp/demandiq-test-venv/bin/python -m pytest -q` → **32 passed**. The feature
tests assert zero-filling, stock forward-fill, Friday/Saturday weekend flags,
traffic/funnel/channel ratios, lags and empty-input safety. The quality tests
assert the model thresholds.

## Live status

`ml_daily_features` remains empty. The extraction works through the portal's
PHP PDO connection (`select 1`, counts, simple filters), but the pooler closes
the connection on the heavier movement/consumption and traffic statements, so
the full 10-dataset read has not completed. Retry once Supabase is stable:

```bash
php portal/scripts/phase5_extract.php 1 1 2026-08-02 > /tmp/phase5_extract.json
python -m cli build-features --org 1 --store 1 --lookback-days 90 --dry-run
python -m cli build-features --org 1 --store 1 --lookback-days 90
```

`PORTAL_ROOT` defaults to `../../portal` relative to the DemandIQ repo and can
be overridden for the EC2 layout.
