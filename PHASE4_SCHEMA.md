# DemandIQ — Phase 4 ML Schema

**Snapshot:** 2026-10-30 (UTC) · organization **1** · store **1**

## Live schema

| Table | Rows | Key / status |
|---|---:|---|
| `sp_demand_forecasts` | 140 | `model_version` **NOT NULL**; `sp_forecasts_unique` on `(organization_id, entity_type, entity_id, forecast_date, model_version)` |
| `sp_stockout_risks` | 10 | `sp_stockout_unique` on `(organization_id, entity_type, entity_id)` |
| `sp_orders` | 646 | old `(organization_id, woocommerce_id)` unique dropped; `sp_orders_org_store_woo_unique` on `(organization_id, store_id, woocommerce_id)` |
| `sp_products` | 39 | `slug`/`permalink` added; 39 slugs backfilled; `sp_products_org_slug_idx` |
| `ml_daily_features` | 0 | store-scoped unique on `(organization_id, store_id, entity_type, entity_id, feature_date)` |
| `ml_model_registry` | 0 | unique on `(organization_id, model_name, model_version)` |
| `ml_prediction_log` | 0 | `model_version` **NOT NULL**; unique on `(organization_id, entity_type, entity_id, forecast_date, model_version)` |
| `ml_calendar_events` | 0 | org nullable; no unique key |

- 39 / 39 products have a slug (backfilled from their stored product payload), e.g. `four-strips-leather-moving-belt`.
- `permalink` is present but empty: the 1.1.0 plugin sends `slug`, not `permalink`; the processor persists it when supplied.
- 6 prior `ml_backfill_jobs` rows; 0 `ml_traffic_mapping_misses`.
- `sp_restock_recommendations` still has no unique key (empty table; the plan keeps accepted/dismissed history).

## App-level follow-through

- `ProductSyncProcessor` persists `slug`/`permalink` from product.sync payloads.
- `InventoryEngine` and the delete-signal path now look up orders by `store_id` as well as `organization_id` + Woo id, so two stores can share order **#100** without touching each other.
- `sp_products.slug` is now available to `traffic_mapping` as the documented fallback; the exact Woo ID still resolved all 30 traffic products.

## Verification

- Portal suite: **132 passed** (661 assertions), including slug persistence and the same Woo order id in two stores.
- DemandIQ suite: **26 passed**, including the ML model/table contract checks.
- Live migrations applied; backup `/tmp/portal-backup-20261030-123847.tgz`.

## What Phase 4 does — and does not — include

Phase 4 is the **training-data schema**. The tables are ready, but `ml_daily_features` is intentionally empty until Phase 5 builds features from orders, movements, stock, BOM and traffic. Actual model training and Modal deployment are Phases 7 and 9; the Modal CLI is already authenticated and ready.
