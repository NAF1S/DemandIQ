# DemandIQ — Phase 1 Data Audit

**Snapshot:** 2026-10-27T19:14:55.919129+00:00  
**Organization:** 1  
**Extraction start:** 2026-05-01

## Database

- **Version:** PostgreSQL 17.11 on x86_64-pc-linux-gnu, compiled by gcc (GCC) 15.2.0, 64-bit
- **DB timezone:** UTC
- **App timezone:** UTC

| table | rows |
| --- | --- |
| sp_organizations | 2 |
| sp_items | 10 |
| sp_products | 39 |
| sp_bom_lines | 126 |
| sp_orders | 637 |
| sp_order_items | 674 |
| sp_order_item_components | 4160 |
| sp_item_movements | 7506 |
| sp_webhook_events | 2814 |
| sp_store_connections | 1 |
| sp_movement_exclusions | 12 |
| sp_demand_forecasts | 140 |
| sp_stockout_risks | 10 |
| sp_restock_recommendations | 0 |

## Orders

- **Window:** 2026-05-11 11:44:10 → 2026-10-26 21:04:20
- **Orders:** 637 (undated: 0)
- **Woo number range:** 868 → 1915 (1048 numbers; 637 rows; ~411 missing)
- **Before 2026-08-02:** 197 · **from 2026-08-02:** 440

| month | orders | revenue |
| --- | --- | --- |
| 2026-05 | 37 | 36493 |
| 2026-06 | 38 | 35284.9 |
| 2026-07 | 120 | 111851 |
| 2026-08 | 175 | 170193 |
| 2026-09 | 131 | 117674 |
| 2026-10 | 136 | 127540 |

## Movements

- **Total:** 7506 · **missing occurred_at:** 0
- **Created:** 2026-08-02 18:37:52 → 2026-10-27 17:11:35
- **Occurred:** 2026-05-16 10:26:55 → 2026-10-27 17:11:35

| movement type | rows | qty delta | reserved delta |
| --- | --- | --- | --- |
| adjustment | 609 | 2857 | 0 |
| confirm | 3722 | -2980 | -1846 |
| initial | 9 | 435 | 0 |
| release | 589 | 0 | -613 |
| reserve | 2383 | 0 | 2459 |
| return | 175 | 179 | 0 |
| sync_fix | 19 | -35 | 0 |

- **Consumption (confirm, qty<0):** 2887 rows · 2980.0000 units · 2026-05-16 → 2026-10-27
- **After exclusions:** 2887 rows · 2980.0000 units

| month | rows | units |
| --- | --- | --- |
| 2026-05 | 68 | 74 |
| 2026-06 | 46 | 46 |
| 2026-07 | 490 | 503 |
| 2026-08 | 937 | 984 |
| 2026-09 | 540 | 551 |
| 2026-10 | 806 | 822 |

## Data flow health

| webhook status | events |
| --- | --- |
| processed | 1964 |
| noop | 850 |

- **Latest plugin event:** #2866 `ord-aefe2f48a671e3b86d0a91fb876e3d55` cancelled · processed · occurred 2026-10-27 12:13:05
- **Organic events (24h / 7d):** 19 / 260
- **Received backlog:** 0 (0 reprocess, 0 delete artifacts, 0 plugin events)
- **Queue jobs / failed jobs:** 0 / 0
- **Store `Hide Haven`:** last seen 2026-10-27 12:15:10 · last sync 2026-10-27 12:15:11 · plugin — · syncs 2551

## Forecast schema

| table | rows |
| --- | --- |
| sp_demand_forecasts | 140 |
| sp_stockout_risks | 10 |
| sp_restock_recommendations | 0 |

- **Unique forecast key present:** False
- **Forecast columns:** 35 · **indexes:** 7

## BOM & stock

| component type | lines |
| --- | --- |
| item | 114 |
| product | 12 |

- **Stock rows:** 45 (10 items, 35 products) · on hand 495.0 · reserved 0.0 · available 495.0 · negative available 0

## Extractors

| extractor | rows | date range | notes |
| --- | --- | --- | --- |
| consumption | 556 | 2026-05-16 → 2026-10-27 | net units 2820.0 |
| order_history | 637 | 2026-05-11T11:44:10 → 2026-10-26T21:04:20 | units 679.0 |
| bom | 126 | — | item + product lines |
| stock_levels | 45 | current | items + products |

## Traffic exports

| file | rows | date range | BOM | invalid dates | notes |
| --- | --- | --- | --- | --- | --- |
| daily_post_product_views.csv | 5391 | 2026-01-01 → 2026-10-26 | no | 0 | duplicate keys: 0 |
| daily_traffic_overview.csv | 365 | 2025-10-27 → 2026-10-26 | no | 0 | duplicate keys: 0 |
| daily_referrers.csv | 857 | 2026-01-01 → 2026-10-26 | no | 0 | duplicate keys: 0 |

- **Product rows:** 3948 · **product groups:** 30
- **Mapped:** 30 by woo id, 0 by title, 0 by slug · **unmatched:** 0
- **Products without product traffic:** 9
  - no traffic: 6 (754) — Kingdushi Series Pop-Up Card Holder Wallet - Midnight Black
  - no traffic: 7 (749) — Kingdushi Series Pop-Up Card Holder Wallet
  - no traffic: 20 (421) — Woven Pattern Moving Belt (Chocolate)
  - no traffic: 22 (414) — Minimalistic Wallet & Dot Moving Belt Combo
  - no traffic: 23 (410) — Basket Pattern Moving Belt (Black+Chocolate)
  - no traffic: 24 (405) — Premium Soft MELA Series Wallet
  - no traffic: 25 (394) — Premium Tri-Fold Wallet (Black)
  - no traffic: 26 (390) — Premium Tri-Fold Wallet (Chocolate)
  - no traffic: 29 (238) — The Omini Leather Money Bag (Black)

- **Site traffic:** 365 days · 2025-10-27 → 2026-10-26 · 103485 views / 38369 visitors · 72 zero days · first non-zero 2026-01-01
- **Referrers:** 857 rows · 17 groups
  - facebook.com: 49964 views
  - instagram.com: 2814 views
  - Search Engines: 734 views
  - com.google.android.googlequicksearchbox: 267 views
  - hidehaven.me: 58 views

## Usable overlap

| scope | start | end | days | valid |
| --- | --- | --- | --- | --- |
| product traffic | 2026-05-11 | 2026-10-26 | 169 | yes |
| site traffic | 2026-05-11 | 2026-10-26 | 169 | yes |

## Findings

- sp_demand_forecasts has no unique key on (organization_id, entity_type, entity_id, forecast_date); Phase 4 must add it before upserts.
- ~411 order numbers in the 868–1915 range have no portal row; trigger Force re-sync in the plugin to check (deleted orders may explain the gaps).
- 9 products have no product-page traffic rows.

