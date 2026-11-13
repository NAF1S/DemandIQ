# Phase 6 — Model selection and architecture

Phase 6 chooses a forecaster per entity from the live 90-day feature matrix. It is deliberately tiered: a cheap baseline for thin history, weekly seasonality for moderate history, and a feature model for dense sales history. Traffic is an optional feature, not a gate.

## Model tiers

| Tier | Model | Version | Requirements | Why |
|---|---|---|---|---|
| 1 | Trailing velocity | `velocity-v2` | 7+ days | Always available; exponential smoothing plus weekly shape |
| 2 | Prophet | `prophet-v2` | 14+ days | Weekly seasonality and 95% intervals; the walk-forward winner for dense item series |
| 3 | LightGBM | `lgbm-traffic-v1` | 20+ non-zero sales days on a product series | Consumes lagged traffic, momentum and rolling features |
| — | Traffic funnel | `funnel-v1` | sparse sales with dense traffic | Two-stage traffic-to-conversion forecast for cold-start products |

NHITS and TimesFM remain deferred. The data volume, the disk budget and the absence of a GPU workload do not justify them yet.

## Traffic is a feature, not a gate

The earlier version of this document treated a positive traffic lead-lag as a prerequisite for LightGBM. That gate was removed. LightGBM now runs on any entity with at least 20 non-zero sales days, whether or not traffic correlates, because the lagged/rolling matrix still adds value and the model can use traffic when it is present.

The traffic lead-lag test remains useful as a diagnostic: `app/models/traffic_signal.py` cross-correlates page views against units over lags −7 to +7 and reports whether traffic leads demand. The Phase 8 ablation is the stronger test: it retrains the same model with and without the raw traffic inputs. The live ablation shows a mean WAPE improvement of 5.49 points from traffic, with large product-level wins and some regressions, which supports per-entity traffic features rather than a global allow/deny rule.

## Selection rules

`app/models/selection.py` applies, in order:

1. Fewer than 5 sales days: `funnel` when traffic is dense or admitted, otherwise `velocity`.
2. Fewer than 14 days: `velocity`.
3. 20 or more sales days on an **item** series: `prophet`. The Phase 8 three-window walk-forward evaluation beat LightGBM on all six high-volume items, so the denser item history now selects the more stable seasonal model.
4. 20 or more sales days on a **product** series: `lightgbm`, with traffic as an optional feature.
5. 14 or more days otherwise: `prophet`.
6. Anything else: `velocity`.

The split is deliberate: item consumption is a single long series where weekly seasonality dominates, while product demand carries the traffic funnel and feature signal that LightGBM was built for. The walk-forward evidence for the change is in [PHASE8_EVALUATION.md](PHASE8_EVALUATION.md).

## Live selection and training

The Phase 6 single-holdout benchmark showed LightGBM winning the five sampled high-volume items on MAE and WAPE. The Phase 8 three-window walk-forward told a different story for the six high-volume items: Prophet won all six, with WAPE between 94.5 and 110.7 versus 112.2 to 226.4 for LightGBM. The difference is the evaluation window, not a data bug, and it is why the selection rule now sends dense item series to Prophet.

The rule change was deployed and run against the live Hide Haven organization on 2026-11-13. The run (`run-20261113T050911Z`) trained all 45 entities in about two minutes and moved items 1–6 from LightGBM to Prophet. Their holdout MAE is now 3.20–3.75 units and WAPE 99.7–131.3, matching the walk-forward advantage rather than the earlier single-holdout LightGBM numbers. Items 7 and 8 were already on Prophet, and items 9 and 10 are empty series that correctly fall back to velocity.

The live selection after that run is 19 Prophet, 16 funnel, 7 velocity, and 3 LightGBM. The three remaining LightGBM models are products 21, 30, and 31, which have dense product-level sales history and traffic features. Stockout risks for all six high-volume items were recalculated under `prophet-v2` in the same run.

One cleanup was necessary: the previous 02:00 UTC cron run had written LightGBM forecasts for those six items, and the new run writes Prophet rows under a different model version rather than overwriting them. Those 84 superseded LightGBM item rows were deleted after confirming no item still had an active LightGBM model, so each item now has a single live forecast series.

## Architecture

- `app/models/features.py` — normalises `ml_daily_features` and rebuilds lags, rolling windows, traffic momentum and the model matrix.
- `app/models/base.py` — `ForecastResult`, `ModelMetrics` and the fit/predict/holdout-backtest contract.
- `app/models/velocity.py`, `prophet_model.py`, `lgbm_model.py`, `funnel.py` — the four forecasters; Prophet and LightGBM are optional imports.
- `app/models/traffic_signal.py` — lead-lag diagnostic.
- `app/models/selection.py` — per-entity choice and lazy model factory.
- `app/training/trainer.py`, `pipeline.py`, `writers.py` — the Phase 7 training run, database writers, risk and recommendation calculations.
- `app/evaluation/backtest.py`, `compare.py`, `backfill.py` — the Phase 8 walk-forward, comparison and actuals backfill.
- `modal_app.py` — the Modal training, evaluation, backfill and trigger functions.
- `portal/scripts/phase6_load_features.php` — reads the persisted feature matrix through the EC2 portal connection.

## Tests

The DemandIQ suite passes with 53 tests and one optional skip. The coverage includes zero-filled feature rebuilding, the positive-lead rule, baseline intervals, funnel conversion, every selection branch — including the new item-versus-product split — registry versioning, the trainer, the risk calculator, the walk-forward folds, candidate selection and traffic stripping.

## Next

Phase 7 is live in [PHASE7_TRAINING.md](PHASE7_TRAINING.md); Phase 8 is in [PHASE8_EVALUATION.md](PHASE8_EVALUATION.md); Phase 9 is in [PHASE9_DEPLOYMENT.md](PHASE9_DEPLOYMENT.md). The next modelling question is whether the three product LightGBM entities should also move to their walk-forward winners, and whether the remaining `baseline-v1` prototype rows should be retired.
