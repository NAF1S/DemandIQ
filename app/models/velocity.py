"""Tier 1 baseline: exponential smoothing with weekly shape and a short trend."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.models.base import BaseForecaster, ForecastResult
from app.models.features import TARGET


class VelocityForecaster(BaseForecaster):
    name = "velocity"
    version = "velocity-v2"
    min_rows = 7

    def __init__(self, alpha: float = 0.3, interval_z: float = 1.96) -> None:
        super().__init__()
        self.alpha = float(alpha)
        self.interval_z = float(interval_z)
        self._sigma = 1.0
        self._weekly: dict[int, float] = {}
        self._last_level = 0.0
        self._last_trend = 0.0
        self._last_date: date | None = None

    def _fit(self, frame: pd.DataFrame) -> None:
        values = frame[TARGET].to_numpy(dtype=float)
        n = len(values)

        levels = np.empty(n)
        level = float(values[0])
        for index, value in enumerate(values):
            level = self.alpha * value + (1.0 - self.alpha) * level
            levels[index] = level

        residual = values - levels
        self._sigma = max(float(np.std(residual)), 0.5)
        self._last_level = float(levels[-1])

        recent = values[-7:]
        prior = values[-14:-7] if n >= 14 else values[: max(1, n - 7)]
        self._last_trend = (float(np.mean(recent)) - float(np.mean(prior))) / 7.0 if len(prior) else 0.0

        overall = float(np.mean(values)) if n else 0.0
        self._weekly = {}
        if overall > 0:
            dow = frame["ds"].dt.day_of_week
            grouped = frame.groupby(dow)[TARGET].mean() / overall
            self._weekly = {
                int(index): float(np.clip(value, 0.5, 1.5)) for index, value in grouped.items()
            }

        self._last_date = frame["ds"].max().date()

    def _predict(self, periods: int, frame: pd.DataFrame) -> list[ForecastResult]:
        if self._last_date is None:
            raise RuntimeError("Velocity model is not fitted.")

        results: list[ForecastResult] = []

        for step in range(1, periods + 1):
            forecast_date = self._last_date + timedelta(days=step)
            weekly = self._weekly.get(forecast_date.weekday(), 1.0)
            predicted = max(0.0, self._last_level + self._last_trend * step) * weekly
            margin = self.interval_z * self._sigma
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
        return {"alpha": self.alpha, "interval_z": self.interval_z}
