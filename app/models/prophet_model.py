"""Tier 2: Prophet v2 with weekly seasonality, holidays and lagged traffic."""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from app.models.base import BaseForecaster, ForecastResult
from app.models.features import PROPHET_REGRESSORS, TARGET

try:  # Prophet is an optional Phase 6 dependency.
    from prophet import Prophet

    PROPHET_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the environment
    Prophet = None  # type: ignore[assignment]
    PROPHET_AVAILABLE = False


class ProphetForecaster(BaseForecaster):
    name = "prophet"
    version = "prophet-v2"
    min_rows = 14

    def __init__(
        self,
        interval_width: float = 0.95,
        regressors: list[str] | None = None,
        holidays: pd.DataFrame | None = None,
    ) -> None:
        super().__init__()
        self.interval_width = float(interval_width)
        self.regressors = regressors
        self.holidays = holidays
        self._model: Any = None
        self._active_regressors: list[str] = []
        self._last_regressors: dict[str, float] = {}
        self._last_date: date | None = None
        self._event_holidays: pd.DataFrame | None = None

    def set_future_calendar(self, frame: pd.DataFrame | None) -> None:
        """Translate known events into Prophet holiday regressors.

        Prophet needs the event calendar at fit time, not predict time, so this
        hook is called before ``fit``. Only days actually flagged as an event
        become holidays; an empty frame leaves Prophet unchanged.
        """
        if frame is None or frame.empty or "ds" not in frame.columns:
            self._event_holidays = None
            return

        holidays = frame.copy()
        holidays["ds"] = pd.to_datetime(holidays["ds"])

        mask = pd.Series(False, index=holidays.index)
        for column in ("is_holiday", "is_festival_period", "is_sale_event", "is_payday"):
            if column in holidays.columns:
                mask |= holidays[column].fillna(False).astype(bool)

        holidays = holidays[mask]
        if holidays.empty:
            self._event_holidays = None
            return

        if "holiday_name" in holidays.columns:
            names = holidays["holiday_name"]
        elif "festival_name" in holidays.columns:
            names = holidays["festival_name"]
        else:
            names = pd.Series("event", index=holidays.index)

        self._event_holidays = pd.DataFrame(
            {
                "ds": holidays["ds"].dt.normalize(),
                "holiday": names.fillna("event").astype(str),
                "lower_window": -1,
                "upper_window": 1,
            }
        ).drop_duplicates(subset=["ds", "holiday"])

    def _fit(self, frame: pd.DataFrame) -> None:
        if not PROPHET_AVAILABLE:
            raise ImportError("prophet is not installed. Run: pip install prophet")

        candidates = self.regressors or PROPHET_REGRESSORS
        self._active_regressors = [name for name in candidates if name in frame.columns]

        training = pd.DataFrame(
            {
                "ds": pd.to_datetime(frame["ds"]),
                "y": frame[TARGET].astype(float),
            }
        )
        for name in self._active_regressors:
            training[name] = pd.to_numeric(frame[name], errors="coerce").fillna(0.0)

        model = Prophet(
            weekly_seasonality=True,
            yearly_seasonality=len(frame) >= 365,
            daily_seasonality=False,
            interval_width=self.interval_width,
            holidays=self.holidays if self.holidays is not None else self._event_holidays,
        )
        for name in self._active_regressors:
            model.add_regressor(name)

        model.fit(training[["ds", "y", *self._active_regressors]])

        self._model = model
        self._last_date = pd.Timestamp(frame["ds"].max()).date()
        self._last_regressors = {
            name: float(training[name].iloc[-1]) for name in self._active_regressors
        }

    def _predict(self, periods: int, frame: pd.DataFrame) -> list[ForecastResult]:
        if self._model is None or self._last_date is None:
            raise RuntimeError("Prophet model is not fitted.")

        future = self._model.make_future_dataframe(periods=periods, freq="D", include_history=False)
        for name in self._active_regressors:
            future[name] = self._last_regressors.get(name, 0.0)

        forecast = self._model.predict(future)
        results: list[ForecastResult] = []

        for row in forecast.itertuples(index=False):
            predicted = max(0.0, float(row.yhat))
            lower = max(0.0, float(row.yhat_lower))
            upper = max(predicted, float(row.yhat_upper))
            results.append(
                ForecastResult(
                    forecast_date=pd.Timestamp(row.ds).date(),
                    predicted_quantity=round(predicted, 4),
                    lower_bound=round(lower, 4),
                    upper_bound=round(upper, 4),
                )
            )

        return results

    def get_hyperparameters(self) -> dict:
        return {
            "weekly_seasonality": True,
            "yearly_seasonality": "auto",
            "interval_width": self.interval_width,
            "regressors": list(self._active_regressors),
        }
