# Phase 8 — Walk-forward evaluation and traffic ablation

## Outcome

The Phase 8 evaluation is complete. A three-window walk-forward comparison ran on all 45 entities with zero model errors, and the full report is stored in the Modal Volume at `reports/eval-20261101T134019Z.json`. It changes the model recommendation for the item series: Prophet, not LightGBM, was the robust winner for the six high-volume items once the evaluation looked at more than the final 14 days.

## Why this approach

The Phase 6 benchmark was a single 14-day holdout. That is enough to compare models on the most recent regime, but it is sensitive to one busy or quiet fortnight. The Phase 8 evaluation uses three expanding-window folds, each with a 14-day test window, and averages the metrics. WAPE is the primary comparison because daily demand is mostly zero, one, or two units; MAPE is unstable at that volume. MAE and pinball loss are recorded alongside it. Traffic is tested by retraining the same model with the raw traffic inputs zeroed, which shows whether the traffic signal earns its place rather than assuming it does.

## What changed

The new evaluation package contains a walk-forward runner, a per-entity model comparison with traffic ablation, and an actual-versus-prediction backfill. The Modal app exposes an `evaluate` function that reads the same feature matrix, writes the comparison to the report volume, and records each candidate with status `evaluated` in the model registry. Those rows are evidence only; they do not compete with the `active` production models.

## The results

The best model by WAPE then MAE was Prophet for 10 entities, funnel for 9, LightGBM for 1, and velocity for 25. The 25 velocity wins are mostly products with no sales in the evaluation windows: every model predicts effectively zero, so the simplest model wins and the WAPE/MAE zeros are degenerate rather than a meaningful quality signal.

The high-volume items tell a clearer story. Prophet beat LightGBM in all six:

| Entity | Prophet WAPE | LightGBM WAPE | Prophet MAE | LightGBM MAE |
|---|---|---|---|---|
| item 1 | **94.5** | 138.1 | **3.27** | 4.74 |
| item 2 | **106.3** | 226.4 | **3.64** | 7.56 |
| item 3 | **97.1** | 139.9 | **3.19** | 4.70 |
| item 4 | **103.1** | 137.8 | **3.48** | 4.57 |
| item 5 | **110.7** | 121.1 | **3.65** | 4.02 |
| item 6 | **102.7** | 112.2 | **3.55** | 3.82 |

This is the main finding: the single-holdout LightGBM advantage from Phase 6 did not generalize across the earlier folds. Prophet's weekly seasonality was more stable for the item series. The production Phase 7 run still used the Phase 6 recommendation, so six items currently carry LightGBM forecasts; the next training decision should use this walk-forward result.

Traffic helped on average. Across the LightGBM and Prophet ablation pairs the mean WAPE improvement from including traffic was **5.49 points**. The product-level wins were concentrated and material: product 21 improved by 23.58 WAPE, product 31 by 9.92, product 30 by 4.66, product 38 by 68.45, and product 33 by 21.66. Some pairs regressed, including product 31's Prophet variant and product 9's Prophet variant, which supports keeping traffic as a per-entity optional feature rather than a global gate. The item series has no product-level traffic, so its comparison is univariate.

The best models by entity are deliberately not a replacement for the production selection. The `evaluated` registry rows are marked separately, and the `active` rows remain one per entity. The report is the evidence for a later “best-of” retrain or a revised selection rule.

## Evidence

The Modal evaluation run completed with exit code 0, 45 entities, 3 splits, and no errors. It wrote 123 evaluated registry rows: 45 velocity, 36 Prophet, 30 funnel, and 12 LightGBM candidates, including the traffic-ablation variants. The DemandIQ suite passes with 52 tests and one optional skip, including the new walk-forward and candidate-selection tests. The report JSON is the authoritative per-entity record; the registry extracts are convenient for SQL review.

## Remaining risks

The production forecasts on the site still use the Phase 6 recommendation, so the item-level forecasts are not the walk-forward winners yet. Changing that requires a deliberate re-selection and a re-run, not an automatic overwrite. The evaluation also inherits the sparse-demand problem: WAPE in the 90–230% range on high-volume items means absolute errors of a few units, not a model that is accurate to a fraction of a unit. The next comparison should keep MAE and pinball loss in view, and should exclude zero-demand entities from any “best model” headline because their zero WAPE is not informative.

## How to verify

Run `modal run modal_app.py::evaluate --organization-id 1 --store-id 1 --periods 14 --n-splits 3`, then inspect the newest `reports/eval-*.json` in the `demandiq-models` volume. The evaluated registry rows are filterable by `status = 'evaluated'`; the production rows remain `status = 'active'`.
