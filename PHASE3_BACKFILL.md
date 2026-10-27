# DemandIQ — Phase 3 Historical Traffic Backfill

**Snapshot:** 2026-10-27 (UTC) · organization **1** · store **1**
**Source:** the three Jetpack daily exports in `web-traffic-data/`

## Imported

| File | Rows | Coverage | Destination |
|---|---:|---|---|
| `daily_post_product_views.csv` | 5,391 | 2026-01-01 → 2026-10-26 | `sp_traffic_daily` (product rows) |
| `daily_traffic_overview.csv` | 365 | 2025-10-27 → 2026-10-26 | `sp_traffic_daily_org` |
| `daily_referrers.csv` | 857 | 2026-01-01 → 2026-10-26 | `sp_traffic_referrers_daily` |

## Product mapping

- **3,948 product-page rows → 3,948 product-day rows** across **30 products**.
- **100% resolved by `woo_product_id`** (`Post_ID`); 0 slug fallbacks, 0 title fallbacks.
- **0 mapping misses** (`ml_traffic_mapping_misses` empty).
- 9 catalog products have no product-page traffic in the export.

## Live tables

| Table | Rows | Key totals |
|---|---:|---|
| `sp_traffic_daily` (`source=jetpack`) | 3,948 | 25,877 product page views · 30 products · 2026-01-11 → 2026-10-26 |
| `sp_traffic_daily` (`source=plugin_realtime`) | 8 | 9 page views / sessions / unique visitors · 2026-10-28 |
| `sp_traffic_daily_org` | 365 | 103,485 views · 38,369 visitors · 4,737 cart page views · 1,711 checkout page views |
| `sp_traffic_referrers_daily` | 857 | 53,902 views · 17 referrer groups |

Top referrers: Facebook 49,964; Instagram 2,814; Search Engines 734.

## Validation

- **Post rows vs overview:** 293 dates compared; **120 dates differ by >2%**, max **68.75%**. The per-post export is **not a full decomposition** of the site overview — product page views are 25,877 of 103,485 site views (~25%). Use the product daily table for product-level demand and the org daily table for site-level traffic; **never sum the two**.
- **Referrer coverage:** 279 dates; 4.76%–100%, average **48.12%** of overview views. Referrer data is a subset; the remainder is direct/unknown.
- **Product mapping:** every `/product/` row resolved or missed — 3,948 resolved, 0 missed.
- **Idempotency:** the second apply left all four traffic counts unchanged (`3,948 / 365 / 857 / 0`); only progress rows were added.

## Backfill jobs

Six `ml_backfill_jobs` rows (three files × two runs), all **done**, no errors:

| File | Rows | Coverage |
|---|---:|---|
| `daily_post_product_views.csv` | 5,391 | 2026-01-01 → 2026-10-26 |
| `daily_traffic_overview.csv` | 365 | 2025-10-27 → 2026-10-26 |
| `daily_referrers.csv` | 857 | 2026-01-01 → 2026-10-26 |

## Notes

- `jetpack` is authoritative through **2026-10-26**; `plugin_realtime` covers **2026-10-27 onward**. Features should prefer Jetpack per `(product, date)`, not sum both sources.
- The mapping already supports `sp_products.slug` when that column is added (Phase 4); today the exact Woo ID resolves all 30 traffic products.
- The plugin's real-time capture is live: 8 product-day rows for 2026-10-28 (9 views/sessions/unique visitors).
