from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from app.training.risk import build_recommendation, build_risk
from app.training.trainer import forecast_rows, train_entity


def training_frame(days: int = 90, base_units: float = 2.0) -> pd.DataFrame:
    dates = pd.date_range("2026-08-01", periods=days, freq="D")
    units = [base_units if index % 3 else 0.0 for index in range(days)]

    return pd.DataFrame(
        {
            "feature_date": dates,
            "units_consumed": units,
            "units_returned": 0.0,
            "order_count": [1 if value > 0 else 0 for value in units],
            "revenue": [value * 10 for value in units],
            "stock_on_hand": 50.0,
            "stock_available": 50.0,
            "page_views": 100,
            "add_to_cart": 5,
        }
    )


def test_train_entity_fits_backtests_and_writes_an_artifact(tmp_path: Path) -> None:
    result = train_entity(
        training_frame(),
        entity_type="item",
        entity_id=7,
        periods=7,
        engine="velocity",
        artifact_dir=tmp_path,
        run_id="test-run",
        organization_id=1,
    )

    assert result.model_name == "velocity"
    assert result.version == "velocity-v2"
    assert len(result.predictions) == 7
    assert result.metrics.mae >= 0
    assert result.metrics.wape >= 0
    assert result.metrics.pinball_loss >= 0
    assert result.artifact_path is not None
    assert Path(result.artifact_path).exists()

    rows = forecast_rows(result, organization_id=1)
    assert len(rows) == 7
    assert rows[0]["entity_type"] == "item"
    assert rows[0]["entity_id"] == 7
    assert rows[0]["model_version"] == "velocity-v2"


def test_risk_is_critical_when_stock_is_exhausted() -> None:
    result = train_entity(
        training_frame(),
        entity_type="item",
        entity_id=7,
        periods=7,
        engine="velocity",
        run_id="test-run",
    )

    risk = build_risk(result, organization_id=1, current_stock=0.0, periods=7)

    assert risk["risk_level"] == "critical"
    assert risk["days_until_stockout"] == 0.0
    assert risk["estimated_stockout_date"] == date.today()

    recommendation = build_recommendation(result, organization_id=1, risk=risk)
    assert recommendation is not None
    assert recommendation["urgency"] == "high"
    assert recommendation["recommended_reorder_qty"] > 0


def test_risk_is_healthy_with_ample_stock() -> None:
    result = train_entity(
        training_frame(),
        entity_type="item",
        entity_id=7,
        periods=7,
        engine="velocity",
        run_id="test-run",
    )

    risk = build_risk(result, organization_id=1, current_stock=1000.0, periods=7)

    assert risk["risk_level"] == "healthy"
    assert risk["days_until_stockout"] is not None
    assert build_recommendation(result, organization_id=1, risk=risk) is None
