"""Freshness monitoring for the DemandIQ pipeline."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd
from psycopg import Connection

DEFAULT_MAX_STALE_DAYS = 2


def feature_span(features: pd.DataFrame) -> tuple[date | None, date | None]:
    if features.empty or "feature_date" not in features.columns:
        return None, None

    dates = pd.to_datetime(features["feature_date"], errors="coerce").dropna()
    if dates.empty:
        return None, None

    return dates.min().date(), dates.max().date()


def assert_horizon_is_forward(
    last_feature_date: date,
    today: date | None = None,
    max_stale_days: int = DEFAULT_MAX_STALE_DAYS,
) -> None:
    """Refuse to train when the feature matrix is too old to forecast forward.

    A one- or two-day lag is normal: today's orders are still arriving and the
    daily build runs before the training pass. Beyond that the horizon would be
    entirely historical, so the run is a mistake rather than a forecast.
    """
    today = today or date.today()
    lag = (today - last_feature_date).days

    if lag > max_stale_days:
        raise RuntimeError(
            f"Feature matrix ends on {last_feature_date.isoformat()}, {lag} days before "
            f"{today.isoformat()}. Run the feature pipeline before training so the "
            f"forecast horizon starts in the future."
        )


def pipeline_health(
    conn: Connection,
    organization_id: int = 1,
    store_id: int = 1,
    max_stale_hours: int = 36,
    today: date | None = None,
) -> dict[str, Any]:
    """Report feature/forecast/risk freshness for monitoring and probes."""
    today = today or date.today()
    now = datetime.now()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT max(feature_date) AS last_feature_date,
                   count(*) AS feature_rows
            FROM ml_daily_features
            WHERE organization_id = %s AND store_id = %s
            """,
            (organization_id, store_id),
        )
        features = cur.fetchone()

        cur.execute(
            """
            SELECT max(updated_at) AS last_forecast_at,
                   count(*) AS forecast_rows,
                   count(*) FILTER (WHERE forecast_date < CURRENT_DATE) AS past_rows
            FROM sp_demand_forecasts
            WHERE organization_id = %s
            """,
            (organization_id,),
        )
        forecasts = cur.fetchone()

        cur.execute(
            """
            SELECT max(calculated_at) AS last_risk_at, count(*) AS risk_rows
            FROM sp_stockout_risks
            WHERE organization_id = %s
            """,
            (organization_id,),
        )
        risks = cur.fetchone()

    last_feature_date = features["last_feature_date"]
    last_forecast_at = forecasts["last_forecast_at"]
    last_risk_at = risks["last_risk_at"]

    feature_lag_days = (today - last_feature_date).days if last_feature_date else None
    forecast_age_hours = (
        round((now - last_forecast_at).total_seconds() / 3600, 1) if last_forecast_at else None
    )
    risk_age_hours = (
        round((now - last_risk_at).total_seconds() / 3600, 1) if last_risk_at else None
    )

    stale = []
    if feature_lag_days is None:
        stale.append("no_feature_rows")
    elif feature_lag_days > DEFAULT_MAX_STALE_DAYS:
        stale.append(f"features_stale_{feature_lag_days}d")

    if forecast_age_hours is None:
        stale.append("no_forecasts")
    elif forecast_age_hours > max_stale_hours:
        stale.append(f"forecasts_stale_{forecast_age_hours}h")

    if risk_age_hours is None:
        stale.append("no_risks")
    elif risk_age_hours > max_stale_hours:
        stale.append(f"risks_stale_{risk_age_hours}h")

    return {
        "status": "stale" if stale else "ok",
        "problems": stale,
        "last_feature_date": last_feature_date.isoformat() if last_feature_date else None,
        "feature_lag_days": feature_lag_days,
        "feature_rows": int(features["feature_rows"] or 0),
        "last_forecast_at": last_forecast_at.isoformat() if last_forecast_at else None,
        "forecast_age_hours": forecast_age_hours,
        "forecast_rows": int(forecasts["forecast_rows"] or 0),
        "past_forecast_rows": int(forecasts["past_rows"] or 0),
        "last_risk_at": last_risk_at.isoformat() if last_risk_at else None,
        "risk_age_hours": risk_age_hours,
        "risk_rows": int(risks["risk_rows"] or 0),
        "max_stale_days": DEFAULT_MAX_STALE_DAYS,
        "max_stale_hours": max_stale_hours,
        "checked_at": now.isoformat(),
    }
