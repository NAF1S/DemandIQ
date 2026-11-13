# Phase 9 — Deployment, scheduling and the Compute now path

## Outcome

DemandIQ is deployed on Modal and the StockPilot portal can start it from the Forecasts page. Modal runs a daily 02:00 UTC training pass, a 06:00 UTC actuals backfill, and an authenticated trigger. The portal stores run history in a new compute-run table, dispatches a queued job, and polls progress until the run succeeds or fails. The first real end-to-end run for the Hide Haven organization completed all 45 entities in 2 minutes 33 seconds and finished at 100% progress.

## Why this approach

The portal should not host the Prophet and LightGBM runtime, and it should not block a web request for several minutes. The Modal trigger returns immediately and spawns the training container; the portal job only hands over the run. The run row then becomes the shared progress record: the portal creates it, the Modal training function updates its entity counts and percentage, and the React button polls it. This also gives cron runs and manual runs the same history instead of two separate execution paths.

The trigger is protected by a shared token in the request body, stored in the Modal secret `demandiq-train-token` and in the portal environment. The Modal web endpoint is reachable without a network-level allowlist, so the token is the authentication boundary and should be rotated if it is exposed.

## What changed

The Modal app now has five functions: the training run, the daily schedule, the evaluator, the actuals backfill, and the trigger. The training function receives an optional compute-run id, and every run created by Modal itself (manual one-off or cron) creates its own history row. The model artifacts remain in the `demandiq-models` Modal Volume.

The portal has a new compute-run table, a model, a queued job, two API routes, and the “Compute now” button. The table records the organization, status, trigger source, entity totals, progress percentage, the Modal run id, the error, the triggering user, and start/finish times. The job posts the organization and compute-run id to the trigger; the Modal function updates the same row as it trains. The button disables while a run is queued or running and shows the percentage and an estimated minutes-remaining value.

The model registry now retires older active versions when a new run succeeds, so there is one active model per entity. The Phase 8 rows remain marked `evaluated` and do not compete with production selection.

## Evidence

The original smoke test used the Test Company administrator (org 2). That organization has no feature rows, so Modal correctly raised “no feature data”; the run row was left looking active for several minutes. The training pipeline now marks an empty-feature run as failed with the reason, and the test row was closed with that explanation. This is why the compute-run table shows one failed org-2 row and one successful org-1 row.

The second smoke test used the real Hide Haven manager (org 1). The API returned `202` with a queued run, the queue job completed in 12 seconds, Modal trained all 45 entities, and the run row advanced from 0 to 100% and finished `succeeded` with the Modal run id `run-20261101T140030Z`. The forecasts were refreshed between 14:00 and 14:02 UTC; the risk and recommendation counts remained 45 and 27, and the active registry was left at 45 rows after the older run was retired. The site returns HTTP 200 at `https://apigateway.live/`.

The portal suite passes with 152 tests and 759 assertions, including the compute-run API, tenant scoping, role enforcement, and the Modal job payload. The DemandIQ suite passes with 52 tests and one optional skip. The SPA type check and production build are clean, and the live server has 1.9 GB free after the earlier storage cleanup.

## Deviations and decisions

The first smoke test was a test-environment mismatch, not an inventory or training fault; the failed row is retained because it is useful evidence of the failure handling. The Modal endpoint uses a token in the JSON body rather than a header; this is explicitly not a network allowlist. The old `baseline-v1` forecast rows remain in the database because the current page does not display them; a future demand chart must filter to the active model version or a cleanup must be deliberate.

## Remaining work and risks

The 02:00 UTC daily run and the 06:00 UTC backfill have been deployed but not yet observed on a real clock, so the next check is the first scheduled run and its compute-run history row. The prediction-log actuals are still empty because the latest feature date is 2026-10-30 and the forecasts start on 2026-10-31; the next feature build plus the 06:00 backfill will populate them. The item-level forecasts should be re-selected against the Phase 8 walk-forward winners before the next production run; that is a deliberate model decision, not an automatic change. The trigger token should be rotated if the portal environment or Modal secret is ever exposed.

## How to verify

Open the Forecasts page and press “Compute now”; confirm that the button shows a percentage and that the compute-run table progresses to `succeeded`. For a direct check, query `sp_compute_runs` for the newest org-1 row, confirm `progress_pct = 100` and `done_entities = total_entities = 45`, then verify that `sp_demand_forecasts` was updated within the same run and that `ml_model_registry` has one active row per entity. The Modal app logs show the training function and the scheduled functions.
