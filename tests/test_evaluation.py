from __future__ import annotations

import pandas as pd

from app.evaluation.backtest import walk_forward_metrics, walk_forward_summary
from app.evaluation.compare import candidate_models, compare_entity, strip_traffic


def evaluation_frame(days: int = 90, units: float = 2.0) -> pd.DataFrame:
    dates = pd.date_range("2026-08-01", periods=days, freq="D")
    values = [units if index % 2 == 0 else 0.0 for index in range(days)]

    return pd.DataFrame(
        {
            "feature_date": dates,
            "units_consumed": values,
            "units_returned": 0.0,
            "order_count": [1 if value > 0 else 0 for value in values],
            "revenue": [value * 10 for value in values],
            "stock_on_hand": 50.0,
            "stock_available": 50.0,
            "page_views": 100,
            "add_to_cart": 5,
        }
    )


def test_walk_forward_velocity_returns_three_folds() -> None:
    metrics = walk_forward_metrics(
        evaluation_frame(),
        "velocity",
        test_periods=7,
        n_splits=3,
    )

    assert len(metrics) == 3
    assert all(item.mae >= 0 for item in metrics)
    assert all(item.wape >= 0 for item in metrics)
    assert all(item.pinball_loss >= 0 for item in metrics)


def test_candidate_models_promotes_lightgbm_for_high_sales() -> None:
    models = candidate_models(evaluation_frame())

    assert "velocity" in models
    assert "prophet" in models
    assert "lightgbm" in models
    assert "funnel" in models


def test_strip_traffic_zeroes_the_raw_inputs() -> None:
    stripped = strip_traffic(evaluation_frame())
    assert stripped["page_views"].sum() == 0
    assert stripped["add_to_cart"].sum() == 0


def test_compare_entity_returns_velocity_for_sparse_sales() -> None:
    frame = evaluation_frame(days=30, units=0.0)
    frame["page_views"] = 0

    report = compare_entity(frame, "item", 3, test_periods=7, n_splits=1)

    assert report["entity_type"] == "item"
    assert report["entity_id"] == 3
    assert "velocity" in report["models"]
    assert report["best_model"] == "velocity"
    assert walk_forward_summary(frame, "velocity")["splits"] >= 0
