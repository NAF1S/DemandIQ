"""Data-quality checks and model recommendation for a daily feature matrix."""

from __future__ import annotations

import pandas as pd


def _max_consecutive_zeros(series: pd.Series) -> int:
    max_run = 0
    run = 0

    for value in series.fillna(0):
        if value == 0:
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0

    return max_run


def _recommend_model(df: pd.DataFrame) -> str:
    non_zero = int((df["net_consumed"] > 0).sum())
    total = len(df)

    if total < 14 or non_zero < 5:
        return "velocity"
    if total >= 60 and non_zero >= 20:
        return "prophet"
    if total >= 30:
        return "lightgbm"
    return "velocity"


def validate_features(
    df: pd.DataFrame,
    entity_id: int,
    entity_type: str | None = None,
) -> dict:
    """Run data-quality checks and return a JSON-safe report."""
    net = df["net_consumed"].fillna(0) if "net_consumed" in df else pd.Series(dtype=float)
    total_days = len(df)
    non_zero_days = int((net > 0).sum())
    mean_daily = float(net.mean()) if total_days else 0.0
    std_daily = float(net.std()) if total_days > 1 else 0.0
    std_daily = 0.0 if pd.isna(std_daily) else std_daily

    report = {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "total_days": total_days,
        "non_zero_days": non_zero_days,
        "sparsity": round(1 - non_zero_days / max(total_days, 1), 4),
        "max_gap_days": _max_consecutive_zeros(net),
        "total_consumed": round(float(net.sum()), 4),
        "mean_daily": round(mean_daily, 4),
        "std_daily": round(std_daily, 4),
        "cv": round(std_daily / max(mean_daily, 0.001), 4),
        "has_enough_data": total_days >= 14 and non_zero_days >= 5,
    }
    report["recommended_model"] = _recommend_model(df) if total_days else "velocity"

    return report
