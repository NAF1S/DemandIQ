"""Traffic-led funnel model for thin-sales entities.

Two stages: forecast page views from their own momentum, then multiply by the
recent view-to-order conversion. This is the cold-start path when orders are
too sparse for a demand model.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.models.base import BaseForecaster, ForecastResult
from app.models.features import TARGET


class FunnelForecaster(BaseForecaster):
    name = "funnel"
    version = "funnel-v1"
    min_rows = 14

    def __init__(self, alpha: float = 0.3, interval_z: float = 1.96) -> None:
        super().__init__()
        self.alpha = float(alpha)
        self.interval_z = float(interval_z)
        self._conversion = 0.0
        self._conversion_sigma = 0.0
        self._traffic_level = 0.0
        self._traffic_sigma = 1.0
        self._weekly: dict[int, float] = {}
        self._last_date: date | None = None

    def _fit(self, frame: pd.DataFrame) -> None:
        traffic = frame["page_views"].to_numpy(dtype=float)
        target = frame[TARGET].to_numpy(dtype=float)

        total_traffic = float(traffic.sum())
        self._conversion = float(target.sum()) / total_traffic if total_traffic > 0 else 0.0

        daily = np.divide(target, traffic, out=np.zeros_like(target), where=traffic > 0)
        observed = daily[traffic > 0]
        self._conversion_sigma = float(np.std(observed)) if len(observed) > 1 else 0.0

        levels = np.empty(len(traffic))
        level = float(traffic[0]) if len(traffic) else 0.0
        for index, value in enumerate(traffic):
            level = self.alpha * value + (1.0 - self.alpha) * level
            levels[index] = level

        self._traffic_level = float(levels[-1]) if len(levels) else 0.0
        self._traffic_sigma = max(float(np.std(traffic - levels)), 0.5) if len(traffic) else 1.0

        overall = float(np.mean(traffic)) if len(traffic) else 0.0
        self._weekly = {}
        if overall > 0:
            dow = frame["ds"].dt.day_of_week
            grouped = frame.groupby(dow)["page_views"].mean() / overall
            self._weekly = {
                int(index): float(np.clip(value, 0.5, 1.5)) for index, value in grouped.items()
            }

        self._last_date = frame["ds"].max().date()

    def _predict(self, periods: int, frame: pd.DataFrame) -> list[ForecastResult]:
        if self._last_date is None:
            raise RuntimeError("Funnel model is not fitted.")

        results: list[ForecastResult] = []

        for step in range(1, periods + 1):
            forecast_date = self._last_date + timedelta(days=step)
            weekly = self._weekly.get(forecast_date.weekday(), 1.0)
            views = max(0.0, self._traffic_level * weekly)
            predicted = views * self._conversion

            traffic_error = self.interval_z * self._traffic_sigma * self._conversion
            conversion_error = self.interval_z * views * self._conversion_sigma
            margin = float(np.sqrt(traffic_error**2 + conversion_error**2))

            results.append(
                ForecastResult(
                    forecast_date=forecast_date,
                    predicted_quantity=round(predicted, 4),
                    lower_bound=round(max(0.0, predicted - margin), 4),
                    upper_bound=round(predicted + margin, 4),
                )
            )

        return results

    def get_hyperparameters(self) -> dict:
        return {
            "alpha": self.alpha,
            "interval_z": self.interval_z,
            "conversion": round(self._conversion, 6),
        }
