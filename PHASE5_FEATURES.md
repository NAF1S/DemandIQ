# Phase 5 — Daily Features

Phase 5 adds the zero-filled daily feature matrix that Phase 6/7 will train on.
It is implemented, tested, and now populated live in `ml_daily_features`.

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
- `app/etl/psql.py` — portal PHP bridge. Reads and writes go through the portal
  PDO connection (`portal/scripts/phase5_extract.php` and
  `phase5_write.php`); pandas performs the feature math.
- `cli.py` — `python -m cli build-features --org 1 --store 1 --lookback-days 90`
  (`--dry-run`, `--json`, `--dsn`).

## Live result

Run from the EC2 host against the live Supabase database:

- 4,050 rows = 45 active entities (10 items, 35 products, store 1) × 90 days
- Date range: 2026-08-02 to 2026-10-30
- 30 products have traffic (1,880 traffic-days; 12,605 views)
- 2,781 rows carry stock snapshots; the rest are products without ledger movements
- 2,413 net units consumed, 2,880 orders and 2,828,566.30 revenue in the window
- 1,125 weekend rows (Friday/Saturday)
- 22 of 45 entities have enough history; recommendations: 9 prophet, 13 lightgbm,
  23 velocity

Largest-traffic sample: product 30 on 2026-08-04, 56 views, 3 orders, 2,397.00
revenue, stock 20, source `{"jetpack": 56}`.

## Why EC2

The local host's Supabase Supavisor pooler drops psql/asyncpg SSL sessions
mid-query on the movement and traffic statements. The EC2 host's portal PHP
connection is stable, so the extraction and load run there. `PORTAL_ROOT` must
point at the deployed portal (`/var/www/stockpilot/portal` on EC2). The EC2 root
disk is at 96% (279 MB free after cleaning apt/journal caches); keep an eye on it.

## Notes

- The portal writer encodes `traffic_sources` as a JSON string; Laravel's upsert
  does not auto-encode arrays and previously raised "Array to string conversion".
- `psql.py` now includes PHP stdout in errors so fatal errors are visible.
- Products without ledger movements have `NULL` stock columns rather than a
  fabricated zero.
- Tests: `/opt/demandiq/.venv/bin/python -m pytest -q` → **32 passed**.

## Phase 6

The training table is ready. The next step is model selection/architecture
(Phase 6), then training and evaluation.
