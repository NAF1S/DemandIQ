"""Tier 3: LightGBM on the full lag/rolling/traffic feature matrix."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from app.models.base import BaseForecaster, ForecastResult
from app.models.features import (
    MODEL_FEATURES,
    STOCK_FEATURES,
    TARGET,
    TRAFFIC_FEATURES,
    feature_matrix,
)

try:  # LightGBM is an optional Phase 6 dependency.
    from lightgbm import LGBMRegressor

    LGBM_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the environment
    LGBMRegressor = None  # type: ignore[assignment]
    LGBM_AVAILABLE = False


class LGBMForecaster(BaseForecaster):
    name = "lightgbm"
    version = "lgbm-traffic-v1"
    min_rows = 30

    def __init__(
        self,
        n_estimators: int = 300,
        learning_rate: float = 0.05,
        num_leaves: int = 15,
        min_child_samples: int = 5,
        interval: bool = True,
    ) -> None:
        super().__init__()
        self.n_estimators = int(n_estimators)
        self.learning_rate = float(learning_rate)
        self.num_leaves = int(num_leaves)
        self.min_child_samples = int(min_child_samples)
        self.interval = bool(interval)
        self._median: Any = None
        self._lower: Any = None
        self._upper: Any = None
        self._importance: dict[str, float] = {}
        self._history: list[float] = []
        self._last_date: date | None = None
        self._traffic: dict[str, float] = {}
        self._stock: dict[str, float] = {}

    def _params(self) -> dict[str, Any]:
        return {
            "n_estimators": self.n_estimators,
            "learning_rate": self.learning_rate,
            "num_leaves": self.num_leaves,
            "min_child_samples": self.min_child_samples,
            "random_state": 42,
            "n_jobs": 1,
            "verbose": -1,
        }

    def _fit(self, frame: pd.DataFrame) -> None:
        if not LGBM_AVAILABLE:
            raise ImportError("lightgbm is not installed. Run: pip install lightgbm")

        matrix = feature_matrix(frame)
        target = frame[TARGET].astype(float).to_numpy()

        self._median = LGBMRegressor(objective="regression", **self._params()).fit(matrix, target)
        if self.interval:
            self._lower = LGBMRegressor(objective="quantile", alpha=0.025, **self._params()).fit(
                matrix, target
            )
            self._upper = LGBMRegressor(objective="quantile", alpha=0.975, **self._params()).fit(
                matrix, target
            )

        self._importance = {
            name: round(float(value), 4)
            for name, value in zip(MODEL_FEATURES, self._median.feature_importances_)
        }
        self._history = [float(value) for value in target]
        self._last_date = pd.Timestamp(frame["ds"].max()).date()
        last = frame.iloc[-1]
        self._traffic = {name: float(last.get(name, 0.0) or 0.0) for name in TRAFFIC_FEATURES}
        self._stock = {name: float(last.get(name, 0.0) or 0.0) for name in STOCK_FEATURES}

    def _future_row(self, forecast_date: date, history: list[float]) -> dict[str, float]:
        def window(days: int) -> list[float]:
            return history[-days:] if len(history) >= days else history

        recent_7 = np.array(window(7), dtype=float)
        recent_14 = np.array(window(14), dtype=float)
        recent_30 = np.array(window(30), dtype=float)

        def mean(values: np.ndarray) -> float:
            return float(values.mean()) if len(values) else 0.0

        def std(values: np.ndarray) -> float:
            return float(values.std()) if len(values) else 0.0

        row = {
            "day_of_week": float(forecast_date.weekday()),
            "is_weekend": 1.0 if forecast_date.weekday() in (4, 5) else 0.0,
            "month": float(forecast_date.month),
            "day_of_month": float(forecast_date.day),
            "is_holiday": 0.0,
            "is_payday": 0.0,
            "is_festival_period": 0.0,
            "lag_1": float(history[-1]) if history else 0.0,
            "lag_7": float(history[-7]) if len(history) >= 7 else 0.0,
            "lag_14": float(history[-14]) if len(history) >= 14 else 0.0,
            "lag_28": float(history[-28]) if len(history) >= 28 else 0.0,
            "rolling_mean_7": mean(recent_7),
            "rolling_mean_14": mean(recent_14),
            "rolling_mean_30": mean(recent_30),
            "rolling_std_7": std(recent_7),
            "rolling_std_14": std(recent_14),
            "rolling_std_30": std(recent_30),
            "rolling_max_7": float(recent_7.max()) if len(recent_7) else 0.0,
            "rolling_max_14": float(recent_14.max()) if len(recent_14) else 0.0,
            "rolling_max_30": float(recent_30.max()) if len(recent_30) else 0.0,
            "trend_7_14": mean(recent_7) - mean(recent_14),
            "trend_7_30": mean(recent_7) - mean(recent_30),
            **self._traffic,
            **self._stock,
        }
        return row

    def _predict(self, periods: int, frame: pd.DataFrame) -> list[ForecastResult]:
        if self._median is None or self._last_date is None:
            raise RuntimeError("LightGBM model is not fitted.")

        history = list(self._history)
        results: list[ForecastResult] = []

        for step in range(1, periods + 1):
            forecast_date = self._last_date + timedelta(days=step)
            row = self._future_row(forecast_date, history)
            matrix = pd.DataFrame([row])[MODEL_FEATURES].astype(float).fillna(0.0)

            predicted = max(0.0, float(self._median.predict(matrix)[0]))
            if self._lower is not None and self._upper is not None:
                lower = max(0.0, float(self._lower.predict(matrix)[0]))
                upper = max(predicted, float(self._upper.predict(matrix)[0]))
            else:
                lower, upper = predicted, predicted

            results.append(
                ForecastResult(
                    forecast_date=forecast_date,
                    predicted_quantity=round(predicted, 4),
                    lower_bound=round(lower, 4),
                    upper_bound=round(upper, 4),
                )
            )
            history.append(predicted)

        return results

    def feature_importance(self) -> dict[str, float]:
        return dict(self._importance)

    def get_hyperparameters(self) -> dict:
        return {**self._params(), "interval": self.interval}
