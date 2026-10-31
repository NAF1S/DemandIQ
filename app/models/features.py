"""Feature contracts shared by the Phase 6 forecasting models.

``ml_daily_features`` stores the base signals. The derived lag/rolling/traffic
columns are rebuilt here so training and inference always use the same matrix.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

TARGET = "net_consumed"
TRAFFIC_SIGNAL_COLUMN = "page_views"

CALENDAR_FEATURES = [
    "day_of_week",
    "is_weekend",
    "month",
    "day_of_month",
    "is_holiday",
    "is_payday",
    "is_festival_period",
]

LAG_FEATURES = ["lag_1", "lag_7", "lag_14", "lag_28"]

ROLLING_FEATURES = [
    "rolling_mean_7",
    "rolling_mean_14",
    "rolling_mean_30",
    "rolling_std_7",
    "rolling_std_14",
    "rolling_std_30",
    "rolling_max_7",
    "rolling_max_14",
    "rolling_max_30",
    "trend_7_14",
    "trend_7_30",
]

TRAFFIC_FEATURES = [
    "views_lag_1",
    "views_lag_7",
    "views_lag_14",
    "views_roll_7",
    "views_roll_14",
    "views_roll_30",
    "views_momentum",
]

STOCK_FEATURES = ["stock_on_hand", "stock_available"]

MODEL_FEATURES = (
    CALENDAR_FEATURES + LAG_FEATURES + ROLLING_FEATURES + TRAFFIC_FEATURES + STOCK_FEATURES
)

PROPHET_REGRESSORS = ["views_lag_1", "views_lag_7", "views_momentum"]


def _numeric(series: pd.Series | None, default: float = 0.0) -> pd.Series:
    if series is None:
        return pd.Series(dtype=float)
    return pd.to_numeric(series, errors="coerce").fillna(default)


def prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise a feature frame and rebuild lags, rolling windows and traffic momentum."""
    if df is None or len(df) == 0:
        raise ValueError("Cannot prepare an empty feature frame.")

    out = df.copy()

    if "ds" not in out.columns:
        if "feature_date" not in out.columns:
            raise ValueError("Feature frame needs 'ds' or 'feature_date'.")
        out["ds"] = out["feature_date"]

    out["ds"] = pd.to_datetime(out["ds"], errors="coerce")
    out = out.dropna(subset=["ds"]).sort_values("ds").reset_index(drop=True)

    out["net_consumed"] = _numeric(out.get("units_consumed")).clip(lower=0)
    out["units_returned"] = _numeric(out.get("units_returned"))
    out["order_count"] = _numeric(out.get("order_count"))
    out["revenue"] = _numeric(out.get("revenue"))
    out["avg_order_value"] = _numeric(out.get("avg_order_value"))
    out["page_views"] = _numeric(out.get("page_views"))
    out["add_to_cart"] = _numeric(out.get("add_to_cart"))
    out["cart_removals"] = _numeric(out.get("cart_removals"))
    out["sessions"] = _numeric(out.get("sessions"))
    out["unique_visitors"] = _numeric(out.get("unique_visitors"))
    out["stock_on_hand"] = _numeric(out.get("stock_on_hand"), np.nan).ffill()
    out["stock_available"] = _numeric(out.get("stock_available"), np.nan).ffill()

    out["day_of_week"] = out["ds"].dt.day_of_week
    out["month"] = out["ds"].dt.month
    out["day_of_month"] = out["ds"].dt.day

    if "is_weekend" in out.columns:
        out["is_weekend"] = out["is_weekend"].fillna(out["day_of_week"].isin([4, 5])).astype(bool)
    else:
        out["is_weekend"] = out["day_of_week"].isin([4, 5])

    for column in ("is_holiday", "is_payday", "is_festival_period"):
        if column not in out.columns:
            out[column] = False
        out[column] = out[column].fillna(False).astype(bool)

    for lag in (1, 7, 14, 28):
        out[f"lag_{lag}"] = out["net_consumed"].shift(lag)

    for window in (7, 14, 30):
        out[f"rolling_mean_{window}"] = out["net_consumed"].rolling(window).mean()
        out[f"rolling_std_{window}"] = out["net_consumed"].rolling(window).std()
        out[f"rolling_max_{window}"] = out["net_consumed"].rolling(window).max()

    out["trend_7_14"] = out["rolling_mean_7"] - out["rolling_mean_14"]
    out["trend_7_30"] = out["rolling_mean_7"] - out["rolling_mean_30"]

    for lag in (1, 7, 14):
        out[f"views_lag_{lag}"] = out["page_views"].shift(lag)

    for window in (7, 14, 30):
        out[f"views_roll_{window}"] = out["page_views"].rolling(window).mean()

    out["views_momentum"] = out["views_roll_7"] - out["views_roll_30"]

    out["view_to_cart"] = np.where(out["page_views"] > 0, out["add_to_cart"] / out["page_views"], 0.0)
    out["cart_to_order"] = np.where(out["add_to_cart"] > 0, out["order_count"] / out["add_to_cart"], 0.0)
    out["view_to_order"] = np.where(out["page_views"] > 0, out["net_consumed"] / out["page_views"], 0.0)
    out["units_per_view"] = np.where(out["page_views"] > 0, out["net_consumed"] / out["page_views"], 0.0)

    return out


def feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Return the model feature columns, filled for LightGBM."""
    missing = [column for column in MODEL_FEATURES if column not in df.columns]
    if missing:
        raise ValueError(f"Feature frame is missing columns: {missing}")
    return df[MODEL_FEATURES].astype(float).fillna(0.0)


def traffic_summary(df: pd.DataFrame) -> dict[str, Any]:
    """Dense-traffic checks used by the selection layer."""
    views = _numeric(df.get(TRAFFIC_SIGNAL_COLUMN))
    nonzero = int((views > 0).sum())
    return {
        "traffic_days": nonzero,
        "traffic_total": float(views.sum()),
        "traffic_rich": nonzero >= 20,
    }
