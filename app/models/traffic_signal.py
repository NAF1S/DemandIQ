"""Traffic lead-lag validation.

Traffic is only admitted as a regressor when it actually leads demand: the
cross-correlation at positive lags must be material and stronger than the
contemporaneous/negative-lag alternatives.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from app.models.features import TARGET, TRAFFIC_SIGNAL_COLUMN, prepare_features


@dataclass
class TrafficSignal:
    admitted: bool
    lag: int | None
    correlation: float
    material: bool
    traffic_days: int
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


def analyse_traffic_signal(
    df: pd.DataFrame,
    max_lag: int = 7,
    material_threshold: float = 0.15,
    min_days: int = 21,
    min_traffic_days: int = 10,
) -> TrafficSignal:
    """Test whether page views lead units at a positive lag."""
    frame = prepare_features(df) if TARGET not in df.columns else df
    target = pd.to_numeric(frame[TARGET], errors="coerce").fillna(0)
    traffic = pd.to_numeric(frame[TRAFFIC_SIGNAL_COLUMN], errors="coerce").fillna(0)

    traffic_days = int((traffic > 0).sum())
    if len(frame) < min_days or traffic_days < min_traffic_days:
        return TrafficSignal(
            admitted=False,
            lag=None,
            correlation=0.0,
            material=False,
            traffic_days=traffic_days,
            reason="insufficient traffic history",
        )

    if target.nunique() < 2 or traffic.nunique() < 2:
        return TrafficSignal(
            admitted=False,
            lag=None,
            correlation=0.0,
            material=False,
            traffic_days=traffic_days,
            reason="zero variance",
        )

    best_lag: int | None = None
    best_corr = 0.0

    for lag in range(-max_lag, max_lag + 1):
        # Positive lag compares today's traffic with demand ``lag`` days ahead,
        # so a positive lag means traffic leads demand.
        corr = target.corr(traffic.shift(-lag))
        if corr is None or pd.isna(corr):
            continue
        if abs(corr) > abs(best_corr):
            best_corr = float(corr)
            best_lag = lag

    material = abs(best_corr) >= material_threshold
    leads = best_lag is not None and best_lag > 0

    if not material:
        reason = "traffic correlation is not material"
    elif not leads:
        reason = "traffic does not lead demand at a positive lag"
    else:
        reason = f"traffic leads demand by {best_lag} day(s)"

    return TrafficSignal(
        admitted=bool(material and leads),
        lag=best_lag,
        correlation=round(best_corr, 4),
        material=material,
        traffic_days=traffic_days,
        reason=reason,
    )
