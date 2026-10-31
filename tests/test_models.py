from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.models.base import ModelMetrics
from app.models.features import MODEL_FEATURES, prepare_features
from app.models.funnel import FunnelForecaster
from app.models.registry import ModelRegistry
from app.models.selection import create_model, select_model
from app.models.traffic_signal import analyse_traffic_signal
from app.models.velocity import VelocityForecaster


def make_frame(days: int = 60, lead: int = 1, traffic: bool = True, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-01-01", periods=days, freq="D")
    pattern = 3 + 2 * np.sin(np.arange(days) * 2 * np.pi / 7)
    target = np.clip(np.round(pattern + rng.normal(0, 0.4, days)), 0, None)

    if not traffic:
        views = np.zeros(days)
    elif lead > 0:
        views = np.clip(pd.Series(target).shift(lead).fillna(0).to_numpy() * 10, 0, None)
    elif lead == 0:
        views = target * 10
    else:
        views = np.clip(pd.Series(target).shift(-1).fillna(0).to_numpy() * 10, 0, None)

    return pd.DataFrame(
        {
            "feature_date": dates,
            "units_consumed": target,
            "units_returned": 0,
            "order_count": target,
            "revenue": target * 100,
            "avg_order_value": 100,
            "stock_on_hand": 20,
            "stock_available": 20,
            "page_views": views,
            "add_to_cart": views * 0.05,
            "cart_removals": 0,
            "sessions": views * 0.5,
            "unique_visitors": views * 0.4,
            "is_weekend": dates.day_of_week.isin([4, 5]),
            "is_holiday": False,
            "is_payday": False,
            "is_festival_period": False,
        }
    )


def test_prepare_features_rebuilds_lags_and_traffic_momentum():
    frame = prepare_features(make_frame(45))

    assert {"lag_1", "lag_7", "rolling_mean_7", "views_lag_1", "views_momentum"} <= set(frame.columns)
    assert frame["net_consumed"].iloc[2] == frame["lag_1"].iloc[3]
    assert frame["views_lag_1"].iloc[1] == frame["page_views"].iloc[0]
    assert frame["day_of_week"].notna().all()
    assert frame["is_weekend"].dtype == bool
    assert set(MODEL_FEATURES) <= set(frame.columns)


def test_traffic_signal_requires_a_positive_lead():
    leading = analyse_traffic_signal(make_frame(60, lead=1))
    same_day = analyse_traffic_signal(make_frame(60, lead=0))

    assert leading.admitted is True
    assert leading.lag == 1
    assert leading.correlation > 0.5
    assert same_day.admitted is False
    assert same_day.lag == 0


def test_traffic_signal_rejects_thin_history():
    signal = analyse_traffic_signal(make_frame(10, lead=1))

    assert signal.admitted is False
    assert signal.traffic_days < 10


def test_velocity_predicts_non_negative_dates_and_intervals():
    model = VelocityForecaster().fit(make_frame(60))
    results = model.predict(3)

    assert len(results) == 3
    assert [r.forecast_date.day for r in results] == [2, 3, 4]
    assert all(r.predicted_quantity >= 0 for r in results)
    assert all(r.lower_bound <= r.predicted_quantity <= r.upper_bound for r in results)


def test_velocity_backtest_returns_metrics():
    metrics = VelocityForecaster().backtest(make_frame(60), horizon=14)

    assert isinstance(metrics, ModelMetrics)
    assert metrics.mae >= 0
    assert metrics.rmse >= metrics.mae - 1e-9
    assert 0 <= metrics.coverage_95 <= 1


def test_funnel_uses_traffic_and_conversion():
    frame = make_frame(30, lead=0)
    frame["units_consumed"] = 0.0
    frame.loc[[5, 12], "units_consumed"] = 5.0

    model = FunnelForecaster().fit(frame)
    results = model.predict(2)

    assert all(r.predicted_quantity >= 0 for r in results)
    assert model.get_hyperparameters()["conversion"] > 0


def test_selection_uses_lightgbm_for_rich_lead_traffic():
    choice = select_model(make_frame(70, lead=1))

    assert choice.model == "lightgbm"
    assert choice.version == "lgbm-traffic-v1"
    assert choice.signal["admitted"] is True


def test_selection_falls_back_to_prophet_without_traffic_signal():
    choice = select_model(make_frame(70, lead=0))

    assert choice.model == "prophet"
    assert choice.signal["admitted"] is False


def test_selection_uses_funnel_for_sparse_sales_with_dense_traffic():
    frame = make_frame(30, lead=0)
    frame["units_consumed"] = 0.0
    frame.loc[[5, 12], "units_consumed"] = 5.0

    choice = select_model(frame)

    assert choice.model == "funnel"


def test_selection_uses_velocity_when_everything_is_thin():
    frame = make_frame(10, lead=1, traffic=False)
    frame["units_consumed"] = 0.0
    frame.loc[[2, 5], "units_consumed"] = 2.0
    frame["page_views"] = 0.0

    choice = select_model(frame)

    assert choice.model == "velocity"


def test_create_model_returns_expected_versions():
    assert create_model("velocity").version == "velocity-v2"
    assert create_model("funnel").version == "funnel-v1"
    with pytest.raises(ValueError):
        create_model("not-a-model")


def test_registry_selects_lowest_mae_and_builds_run_versions():
    registry = ModelRegistry()
    best = registry.select_best(
        {
            "velocity": {"mae": 1.4},
            "prophet": {"mae": 1.1},
            "lightgbm": {"mae": 1.8},
        }
    )
    version = registry.run_version(VelocityForecaster(), "product", 30)

    assert best == "prophet"
    assert version.startswith("velocity-v2-product-30-")
    assert registry.artifact_path(1, "velocity", version, "product", 30).endswith(
        "product-30.joblib"
    )


def test_prophet_and_lightgbm_when_available():
    frame = make_frame(70, lead=1)

    prophet = pytest.importorskip("prophet")
    from app.models.prophet_model import ProphetForecaster

    prophet_model = ProphetForecaster(regressors=[]).fit(frame)
    assert len(prophet_model.predict(2)) == 2

    pytest.importorskip("lightgbm")
    from app.models.lgbm_model import LGBMForecaster

    lgbm = LGBMForecaster(n_estimators=30).fit(frame)
    results = lgbm.predict(2)
    assert len(results) == 2
    assert sum(lgbm.feature_importance().values()) > 0
