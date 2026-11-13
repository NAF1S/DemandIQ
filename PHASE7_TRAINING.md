# Phase 7 — Training and the first live forecasts

## Outcome

DemandIQ now has a production training path that runs on Modal and writes directly to the live Supabase prediction tables. The first run trained all 45 entities, produced 630 daily forecasts, 45 stockout-risk assessments, 27 pending restock recommendations, 45 active model-registry entries, and 630 prediction-log rows. Supabase remains the system of record; Modal holds the compute and the joblib model artifacts.

## Why this approach

The Phase 5 feature matrix already lives in Supabase, and the Phase 6 selection rules choose a model per entity. Training needed two things the portal should not own: the Prophet and LightGBM runtime, and a run history that survives the training container. Modal supplies both without moving the feature data out of the database. The Modal function connects to Supabase through the pooler with prepared statements disabled, reads the existing 4,050 feature rows, trains each entity, and writes the results in the same transaction as each entity's forecast and risk.

The forecast horizon is 14 days. The model is fitted on the full 90-day window after a 14-day holdout backtest; the backtest metrics are stored with the model version so the choice can be reviewed later. The risk assessment uses the entity's current available stock (`quantity_on_hand - quantity_reserved`), the mean forecast demand, and the same critical/warning thresholds the Forecasts page already displays.

## What changed

A pure per-entity trainer selects the Phase 6 model, runs the holdout backtest, fits the final model, predicts the horizon, and writes a joblib artifact. The selection is the recommendation from the earlier benchmark: LightGBM for entities with at least 20 non-zero sales days, Prophet for 14–19, funnel for sparse sales with dense traffic, and the velocity baseline otherwise.

A database layer writes the four output surfaces idempotently: demand forecasts (`sp_demand_forecasts`), stockout risks (`sp_stockout_risks`), restock recommendations (`sp_restock_recommendations`), and the model registry and prediction audit (`ml_model_registry`, `ml_prediction_log`). Recommendations replace only the pending row for an entity, so accepted or dismissed recommendations are preserved. Previous active model versions are retired when a new run succeeds, leaving one active model per entity.

The Modal app (`modal_app.py`) defines the training function, a daily 02:00 UTC schedule, a 06:00 UTC actuals backfill, the Phase 8 evaluator, and the authenticated trigger used by the portal. The model artifacts and run summaries live in the Modal Volume `demandiq-models`.

## The first run

The first run (`run-20261101T133526Z`) processed 4,050 feature rows across 45 entities in about 90 seconds. It selected 9 LightGBM, 13 Prophet, 16 funnel, and 7 velocity models. All 45 entities trained; there were no failures.

| Surface | Rows | Meaning |
|---|---|---|
| Daily forecasts | 630 | 45 entities × 14 forecast days, dated 2026-10-31 to 2026-11-13 |
| Stockout risks | 45 | 26 critical, 1 warning, 18 healthy |
| Restock recommendations | 27 | 26 high urgency, 1 medium; all pending |
| Active registry entries | 45 | One active model per entity after retiring the earlier version |
| Prediction log | 630 | One row per forecast, grouped under the run |

The high-volume items all used LightGBM in this run. Their holdout backtest produced MAE values between 2.43 and 3.03 and WAPE between 75.5% and 94.2% — better than the Prophet and velocity baselines in the earlier Phase 6 comparison. The Phase 8 walk-forward evaluation later showed that this single-holdout advantage did not hold across earlier windows; that result is covered in [PHASE8_EVALUATION.md](PHASE8_EVALUATION.md) and does not change what this run wrote.

The live forecast rows were written between 13:35 and 13:37 UTC. The Modal Volume contains one joblib artifact per entity and a JSON summary at `runs/run-20261101T133526Z.json`.

## Manual compute path

The portal now has a `sp_compute_runs` table and a “Compute now” path: the API creates a run, a queued job posts the run to the Modal trigger, Modal spawns the training function, and the function updates the run's progress. The first end-to-end smoke test for the real Hide Haven organization (org 1) completed 45 of 45 entities in 2 minutes 33 seconds and marked the run `succeeded` with the Modal run id `run-20261101T140030Z`. The run refreshed all 630 forecasts, the 45 risks, and the 27 recommendations.

The first smoke test accidentally used the unrelated Test Company administrator (org 2), which has no feature rows. That run was marked failed with an explicit explanation rather than left running. Training now also closes the compute-run row when the feature set is empty, so this cannot look like progress indefinitely.

## Evidence

The DemandIQ unit suite passes with 52 tests and one optional skip; the new tests cover the trainer, the risk calculator, the walk-forward folds, candidate selection, traffic stripping, and the computed forecast/risk rows. The portal suite passes with 152 tests and 759 assertions, including the compute-run API, tenant scoping, role enforcement, and the Modal job payload. The SPA type check and production build are clean.

The live database confirms the run: 630 new forecasts across the four model versions, 45 risk rows, 27 pending recommendations, 45 active registry rows, and 630 prediction-log rows. The site returns HTTP 200 at `https://apigateway.live/`.

## Remaining risks

The 140 prototype `baseline-v1` forecast rows from the old worker remain in the table; the current Forecasts page does not render a demand chart, so they are not visible, but a future chart must filter or retire them. The prediction log has no actuals yet because the feature window ends on 2026-10-30 and the 06:00 UTC backfill has not run; the backfill will populate them after the next feature build. The single-holdout selection and the Phase 8 walk-forward result disagree for the high-volume item series; the next training decision should use the walk-forward comparison rather than silently keeping the current selection.
