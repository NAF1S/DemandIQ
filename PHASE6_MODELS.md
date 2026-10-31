# Phase 6 — Model Selection & Architecture

Phase 6 chooses a forecaster per entity from the live 90-day feature matrix.
The design is deliberately tiered: cheap baselines stay available for sparse
SKUs, weekend seasonality covers moderate history, and LightGBM is admitted only
when traffic demonstrably leads demand.

## Model tiers

| Tier | Model | Version | Requirements | Why |
|---|---|---|---|---|
| 1 | Trailing velocity | `velocity-v2` | 7+ days | Always available; exponential smoothing + weekly shape |
| 2 | Prophet | `prophet-v2` | 14+ days | Weekly seasonality, 95% intervals, holiday regressors |
| 3 | LightGBM | `lgbm-traffic-v1` | 60+ days / 20+ sales days / admitted traffic | Consumes lagged traffic, momentum and rolling features |
| — | Traffic funnel | `funnel-v1` | sparse sales + dense traffic | Two-stage traffic → conversion for cold-start SKUs |

NHITS/TimesFM remain deferred to Phase 9 GPU work; the data volume and disk
budget do not justify them yet.

## Traffic lead-lag gate

`app/models/traffic_signal.py` cross-correlates page views against units over
lags `-7..+7`. A positive lag means traffic leads demand. Traffic is admitted
only when the best correlation is material (|r| ≥ 0.15) and positive.

Live result: **5 of 45 entities** passed the gate. This is why the selection did
not pick LightGBM automatically even though the backtest below shows it winning
on the dense-traffic items.

## Selection rules

`app/models/selection.py` applies, in order:

1. Fewer than 5 sales days: `funnel` when traffic is dense or admitted, else `velocity`.
2. Fewer than 14 days: `velocity`.
3. 60+ days, 20+ sales days, admitted traffic and dense traffic: `lightgbm`.
4. 14+ days otherwise: `prophet`.
5. Anything else: `velocity`.

Quality (sparsity, gaps, CV) comes from Phase 5; the traffic gate and traffic
density are Phase 6 additions.

## Live selection (EC2, 4,050 feature rows)

- 45 entities: 22 `prophet`, 16 `funnel`, 7 `velocity`, 0 `lightgbm`
- 5 entities have an admitted traffic lead
- 5-entity backtest (14-day holdout, top entities by units):

| Entity | Velocity MAE | Prophet MAE | LightGBM MAE |
|---|---|---|---|
| item 1 | 4.99 | 3.20 | **2.90** |
| item 2 | 4.52 | 3.50 | **3.03** |
| item 4 | 4.34 | 3.30 | **2.93** |
| item 5 | 5.23 | 3.75 | **2.65** |
| item 6 | 4.85 | 3.42 | **2.43** |

LightGBM wins every sampled entity on MAE, Prophet improves on velocity, and
MAPE is high across the board because daily actuals are 0/1/2. Phase 7 should
use the backtest as the final arbiter and lead with MAE/MASE, not MAPE.

## Architecture

- `app/models/features.py` — normalises `ml_daily_features`, rebuilds lags,
  rolling windows, traffic momentum and the model matrix.
- `app/models/base.py` — `ForecastResult`, `ModelMetrics`, and the
  fit/predict/holdout-backtest ABC.
- `app/models/velocity.py`, `prophet_model.py`, `lgbm_model.py`, `funnel.py` —
  the four forecasters; Prophet and LightGBM are optional imports.
- `app/models/traffic_signal.py` — lead-lag admission test.
- `app/models/selection.py` — per-entity choice and lazy model factory.
- `app/models/registry.py` — joblib artifacts in Supabase Storage, run tracking
  in `ml_model_registry`, and lowest-MAE selection.
- `portal/scripts/phase6_load_features.php` — reads the persisted feature matrix
  through the EC2 portal connection.
- `python -m cli select-models --org 1 --store 1 --backtest --limit 5` — live
  selection and bake-off.

## Tests

- Local: **44 passed, 1 skipped** (optional Prophet/LightGBM).
- EC2: **44 passed, 1 skipped**.
- The suite covers zero-filled feature rebuilding, the positive-lead rule,
  baseline intervals, funnel conversion, all selection branches, registry
  versioning and optional model smoke tests.

## Next

Phase 7 turns this into training runs: fit the chosen models, write
`sp_demand_forecasts`, log predictions, and produce stockout risk/restock
recommendations. The live 90-day window and 45 entities are ready.
