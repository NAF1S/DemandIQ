import pandas as pd

from app.etl.quality import validate_features


def feature_series(total_days: int, non_zero: int) -> pd.DataFrame:
    values = [1.0] * non_zero + [0.0] * (total_days - non_zero)
    return pd.DataFrame(
        {
            "ds": pd.date_range("2026-01-01", periods=total_days, freq="D"),
            "net_consumed": values,
        }
    )


def test_sparse_series_recommends_velocity():
    report = validate_features(feature_series(10, 1), 42, "item")

    assert report["total_days"] == 10
    assert report["non_zero_days"] == 1
    assert report["sparsity"] == 0.9
    assert report["max_gap_days"] == 9
    assert report["has_enough_data"] is False
    assert report["recommended_model"] == "velocity"
    assert report["entity_id"] == 42
    assert report["entity_type"] == "item"


def test_moderately_dense_series_recommends_lightgbm():
    report = validate_features(feature_series(30, 10), 7, "product")

    assert report["has_enough_data"] is True
    assert report["recommended_model"] == "lightgbm"
    assert report["mean_daily"] == round(10 / 30, 4)
    assert report["cv"] > 0


def test_long_dense_series_recommends_prophet():
    report = validate_features(feature_series(90, 30), 8, "product")

    assert report["has_enough_data"] is True
    assert report["recommended_model"] == "prophet"


def test_fourteen_days_with_five_nonzero_stays_velocity():
    report = validate_features(feature_series(14, 5), 9, "item")

    assert report["has_enough_data"] is True
    assert report["recommended_model"] == "velocity"
