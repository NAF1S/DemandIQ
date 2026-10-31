"""Traffic-aware model selection for one entity."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from app.etl.quality import validate_features
from app.models.features import prepare_features, traffic_summary
from app.models.traffic_signal import TrafficSignal, analyse_traffic_signal


@dataclass
class ModelChoice:
    model: str
    reason: str
    version: str
    quality: dict[str, Any]
    traffic: dict[str, Any]
    signal: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


VERSIONS = {
    "velocity": "velocity-v2",
    "prophet": "prophet-v2",
    "lightgbm": "lgbm-traffic-v1",
    "funnel": "funnel-v1",
}


def select_model(
    features: pd.DataFrame,
    quality: dict[str, Any] | None = None,
    signal: TrafficSignal | None = None,
) -> ModelChoice:
    """Pick the cheapest model that the data can support.

    Rules (traffic-aware):
    - fewer than 5 sales days: funnel when traffic is dense, else velocity
    - fewer than 14 days or moderate history: velocity
    - 60+ days, 20+ sales days and admitted traffic: LightGBM
    - 30+ days otherwise: Prophet
    """
    frame = prepare_features(features)
    quality = quality or validate_features(frame, 0)
    signal = signal or analyse_traffic_signal(frame)
    traffic = traffic_summary(frame)

    total_days = int(quality["total_days"])
    sales_days = int(quality["non_zero_days"])

    if sales_days < 5:
        if traffic["traffic_rich"] or signal.admitted:
            model = "funnel"
            reason = "thin sales with dense traffic; use the traffic funnel"
        else:
            model = "velocity"
            reason = "thin sales and thin traffic; use the velocity baseline"
    elif total_days < 14:
        model = "velocity"
        reason = "fewer than 14 days of history"
    elif total_days >= 60 and sales_days >= 20 and signal.admitted and traffic["traffic_rich"]:
        model = "lightgbm"
        reason = "rich sales and admitted lead-lag traffic support LightGBM"
    elif total_days >= 30:
        model = "prophet"
        reason = "moderate history; weekly seasonality is the safer choice"
    else:
        model = "velocity"
        reason = "not enough history for a seasonal or feature model"

    return ModelChoice(
        model=model,
        reason=reason,
        version=VERSIONS[model],
        quality=quality,
        traffic=traffic,
        signal=signal.as_dict(),
    )


def create_model(name: str, **kwargs: Any):
    """Lazy model factory so optional dependencies are only imported when needed."""
    if name == "velocity":
        from app.models.velocity import VelocityForecaster

        return VelocityForecaster(**kwargs)
    if name == "funnel":
        from app.models.funnel import FunnelForecaster

        return FunnelForecaster(**kwargs)
    if name == "prophet":
        from app.models.prophet_model import ProphetForecaster

        return ProphetForecaster(**kwargs)
    if name == "lightgbm":
        from app.models.lgbm_model import LGBMForecaster

        return LGBMForecaster(**kwargs)
    raise ValueError(f"Unknown model: {name}")
